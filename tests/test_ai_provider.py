import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import ai_provider as ai
import ai_review


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.rootpatch = patch.object(ai.journal, 'ROOT', self.root)
        self.rootpatch.start()
        self.envpatch = patch.dict(ai.os.environ, {'DEEPSEEK_API_KEY': ''})
        self.envpatch.start()

    def tearDown(self):
        self.rootpatch.stop()
        self.envpatch.stop()
        self.folder.cleanup()

    def test_key_private_atomic_and_blank_preserves(self):
        self.assertFalse(ai.status()['available'])
        result = ai.save({'api_key': 'sk-fixture-private-key'})
        self.assertNotIn('sk-fixture', json.dumps(result))
        self.assertEqual(ai.config_path().stat().st_mode & 0o777, 0o600)
        ai.save({'api_key': ''})
        self.assertEqual(ai.config()['api_key'], 'sk-fixture-private-key')
        ai.save({'model': 'user-selected-model'})
        self.assertEqual(ai.config()['model'], 'user-selected-model')
        with self.assertRaises(ValueError): ai.save({'model': 'bad\nmodel'})
        with self.assertRaises(ValueError): ai.save({'api_key': 'bad\nAuthorization: leak'})

    def test_native_images_and_schema_validation(self):
        ai.save({'api_key': 'sk-fixture-private-key'})
        image = self.root / 'static/assets/example.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'fixture-png')
        report = {'title':'测试','summary':'证据待核对','facts':[],'risks':[],'questions':[],'next_review':[]}
        result = {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(report)}}], 'usage':{'total_tokens':10}}
        with patch.object(ai, 'request', return_value=result) as call:
            data, meta = ai.complete('交易证据', ai_review.SCHEMA, [image])
        self.assertEqual(data, report)
        self.assertEqual(meta['images_used'], ['/assets/example.png'])
        self.assertEqual(meta['provider'], 'DeepSeek')
        payload = call.call_args.args[1]
        self.assertTrue(payload['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('sk-fixture', json.dumps(payload))
        with patch.object(ai, 'request', return_value={'choices':[{'finish_reason':'length','message':{'content':json.dumps(report)}}]}):
            with self.assertRaisesRegex(ValueError, '未完整结束'): ai.complete('证据', ai_review.SCHEMA)
        with patch.object(ai, 'request', return_value={'choices':[{'finish_reason':'stop','message':{'content':'{"title":"only"}'}}]}):
            with self.assertRaisesRegex(ValueError, '字段不完整'): ai.complete('证据', ai_review.SCHEMA)

    def test_path_escape_and_redirect_fail_closed(self):
        path = self.root / 'private.png'; path.write_bytes(b'private')
        ai.save({'api_key':'sk-fixture-private-key'})
        with patch.object(ai, 'request') as call:
            with self.assertRaises(ValueError): ai.complete('证据', ai_review.SCHEMA, [path])
            call.assert_not_called()
        with self.assertRaises(ValueError): ai.NoRedirect().redirect_request(None)

    def test_http_error_never_echoes_provider_body_or_key(self):
        ai.save({'api_key':'sk-fixture-private-key'})
        error = urllib.error.HTTPError(ai.BASE+'/models', 401, 'sk-fixture-private-key', {}, io.BytesIO(b'secret body'))
        with patch.object(ai.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(ValueError, 'API Key 无效') as caught: ai.check()
        self.assertNotIn('sk-fixture', str(caught.exception))
        self.assertNotIn('secret body', str(caught.exception))

    def test_model_discovery_is_not_a_generated_review(self):
        ai.save({'api_key':'sk-fixture-private-key'})
        with patch.object(ai, 'request', return_value={'data':[{'id':ai.DEFAULT_MODEL}]}) as call:
            result = ai.check()
        self.assertEqual(call.call_args.args, ('/models',))
        self.assertIn('tested_at', result)
        self.assertNotIn('api_key', result)


    def test_legacy_migration_and_profile_key_isolation(self):
        ai.config_path().parent.mkdir(parents=True)
        ai.config_path().write_text(json.dumps({'provider':'deepseek','model':ai.DEFAULT_MODEL,'api_key':'sk-legacy-private-key'}))
        self.assertEqual(ai.config()['api_key'], 'sk-legacy-private-key')
        ai.save({'provider':'openai','model':'user-model'})
        self.assertFalse(ai.status()['available'])
        self.assertEqual(ai.config()['api_key'], '')
        ai.save({'api_key':'sk-openai-private-key'})
        ai.save({'provider':'deepseek'})
        self.assertEqual(ai.config()['api_key'], 'sk-legacy-private-key')
        self.assertNotIn('private-key', json.dumps(ai.status()))
        self.assertEqual(json.loads(ai.config_path().read_text())['version'], 2)
        ai.save({'provider':'custom','base_url':'https://gateway.example/v1','model':'org/custom','api_key':'gateway-private-key'})
        ai.save({'provider':'custom','base_url':'https://different.example/v1','model':'org/custom'})
        self.assertFalse(ai.status()['available'])
        self.assertEqual(ai.config()['api_key'], '')
        with self.assertRaises(ValueError):ai.save({'provider':'openai','base_url':'https://gateway.example/v1'})

    def test_public_https_only_and_pinned_dns(self):
        for address in ['http://public.example/v1','https://localhost','https://10.0.0.1/v1','https://127.0.0.1','https://[::1]','https://user:secret@public.example','https://public.example?key=secret','https://public.example/#x','https://x.local','https://public.example/%2e%2e']:
            with self.subTest(address=address), self.assertRaises(ValueError):ai.normalized_url(address)
        self.assertEqual(ai.normalized_url('https://public.example:443/v1/'), 'https://public.example/v1')
        with patch.object(ai.socket,'getaddrinfo',return_value=[(2,1,6,'',('1.1.1.1',443)),(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):ai.public_addresses('public.example',443)
        with patch.object(ai,'public_addresses',return_value=['1.1.1.1']) as resolver, patch.object(ai.socket,'create_connection') as connect:
            context=ai.ssl.create_default_context()
            with patch.object(context,'wrap_socket') as wrap:
                conn=ai.PinnedHTTPSConnection('public.example',context=context)
                conn.connect()
                resolver.assert_called_once_with('public.example',443)
                self.assertEqual(connect.call_args.args[0],('1.1.1.1',443))
                self.assertEqual(wrap.call_args.kwargs['server_hostname'],'public.example')

    def test_protocols_and_capability_flags(self):
        report={'title':'测试','summary':'仅依据记录','facts':[],'risks':[],'questions':[],'next_review':[]}
        native={'anthropic':{'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(report)}]},
                'openai':{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(report)}]}]},
                'gemini':{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(report)}}]}}
        image=self.root/'static/assets/example.png';image.parent.mkdir(parents=True);image.write_bytes(b'fixture')
        for provider,expected_path in [('anthropic','/messages'),('openai','/responses'),('gemini','/chat/completions')]:
            with self.subTest(provider=provider):
                ai.save({'provider':provider,'model':'chosen-model','api_key':'sk-fixture-private-key'})
                with patch.object(ai,'request',return_value=native[provider]) as call:
                    actual,meta=ai.complete('evidence',ai_review.SCHEMA,[image])
                self.assertEqual(actual,report);self.assertEqual(meta['images_used'],['/assets/example.png'])
                self.assertEqual(call.call_args.args[0],expected_path)
                payload=call.call_args.args[1]
                self.assertNotIn('thinking',payload)
                if provider=='anthropic':self.assertEqual(payload['messages'][0]['content'][1]['source']['type'],'base64')
                if provider=='openai':self.assertEqual(payload['input'][0]['content'][1]['type'],'input_image')
        ai.save({'provider':'custom','model':'text-only','base_url':'https://public.example/v1','supports_images':False,'json_mode':False,'api_key':'sk-fixture-private-key'})
        with patch.object(ai,'request',return_value=native['gemini']) as call:
            _,meta=ai.complete('evidence',ai_review.SCHEMA,[image])
        self.assertEqual(meta['images_used'],[]);self.assertEqual(meta['images_omitted'],1)
        self.assertEqual(len(call.call_args.args[1]['messages'][1]['content']),1)
        self.assertIn('未发送截图',call.call_args.args[1]['messages'][1]['content'][0]['text'])
        self.assertNotIn('response_format',call.call_args.args[1])

    def test_model_listing_uses_unsaved_profile_without_switching_active(self):
        ai.save({'api_key':'sk-fixture-private-key'})
        with patch.object(ai,'request',return_value={'data':[{'id':'claude-user-model','display_name':'Claude'}]}) as call:
            result=ai.models({'provider':'anthropic','api_key':'anthropic-private-key'})
        self.assertEqual(call.call_args.kwargs['settings']['api_key'],'anthropic-private-key')
        self.assertEqual(result['models'][0]['id'],'claude-user-model')
        self.assertEqual(ai.config()['provider'],'deepseek')
        self.assertIn('不调用模型',result['message'])

    def test_auth_headers_follow_protocol_and_errors_remain_private(self):
        for provider in ['anthropic','openai']:
            ai.save({'provider':provider,'model':'chosen','api_key':'sk-fixture-private-key'})
            with patch.object(ai.urllib.request,'build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'{"data":[]}'
                ai.models()
                request=opener.return_value.open.call_args.args[0]
                headers={k.lower():v for k,v in request.headers.items()}
                if provider=='anthropic':
                    self.assertEqual(headers['x-api-key'],'sk-fixture-private-key');self.assertNotIn('authorization',headers)
                else:
                    self.assertEqual(headers['authorization'],'Bearer sk-fixture-private-key');self.assertNotIn('x-api-key',headers)
