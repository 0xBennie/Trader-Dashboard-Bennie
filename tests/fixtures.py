"""Synthetic journals for isolated tests; never load a real account database."""
import journal

def create_journal():
    item = journal.save_journal({"symbol": "ETHUSDT", "title": "Synthetic journal", "emotion": "未记录"})
    with journal.connect() as db:
        db.execute("UPDATE journals SET id=? WHERE id=?", ("fixture-eth", item["id"]))
    return "fixture-eth"
