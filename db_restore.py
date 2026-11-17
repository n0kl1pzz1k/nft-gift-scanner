"""Restore a verified SQLite snapshot to a new path without overwriting files."""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, tempfile
from pathlib import Path
from typing import Any

def _sha256_file(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""): digest.update(chunk)
    return digest.hexdigest()

def _schema_sha(connection: sqlite3.Connection) -> str:
    rows=connection.execute("SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    data=[{"type":r[0],"name":r[1],"table":r[2],"sql":r[3]} for r in rows]
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def _counts(connection: sqlite3.Connection) -> dict[str,int]:
    names=connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    result={}
    for (name,) in names:
        quoted='"'+str(name).replace('"','""')+'"'
        result[str(name)]=int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
    return result

def _sequences(connection: sqlite3.Connection) -> dict[str,int]:
    try: rows=connection.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name").fetchall()
    except sqlite3.OperationalError: return {}
    return {str(name):int(seq) for name,seq in rows}

def _inspect(connection: sqlite3.Connection) -> dict[str,Any]:
    return {
        "integrity_check":str(connection.execute("PRAGMA integrity_check").fetchone()[0]),
        "foreign_key_violations":len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        "schema_sha256":_schema_sha(connection),
        "tables":_counts(connection),
        "sequence_counters":_sequences(connection),
    }

def restore_database(backup_path: str|Path, manifest_path: str|Path, destination_path: str|Path) -> dict[str,Any]:
    backup=Path(backup_path).expanduser().resolve(strict=True)
    manifest_file=Path(manifest_path).expanduser().resolve(strict=True)
    destination=Path(destination_path).expanduser().resolve()
    if destination in {backup,manifest_file}: raise ValueError("Restore destination must differ from backup and manifest")
    if destination.exists(): raise FileExistsError(f"Restore destination already exists: {destination}")
    manifest=json.loads(manifest_file.read_text(encoding="utf-8"))
    if _sha256_file(backup)!=manifest.get("backup_sha256"): raise RuntimeError("Backup SHA256 does not match manifest")
    source=sqlite3.connect(backup.as_uri()+"?mode=ro",uri=True,timeout=30)
    temporary=None; target=None
    try:
        source_report=_inspect(source)
        expected={"integrity_check":manifest.get("integrity_check"),"foreign_key_violations":manifest.get("foreign_key_violations"),"schema_sha256":manifest.get("schema_sha256"),"tables":manifest.get("tables"),"sequence_counters":manifest.get("sequence_counters",{})}
        if source_report!=expected: raise RuntimeError("Backup contents do not match manifest metadata")
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd,temp_name=tempfile.mkstemp(prefix=f".{destination.name}.",suffix=".restore.tmp",dir=str(destination.parent)); os.close(fd)
        temporary=Path(temp_name); target=sqlite3.connect(str(temporary),timeout=30)
        source.backup(target,pages=256,sleep=0.05); target.commit()
        restored=_inspect(target)
        if restored!=source_report: raise RuntimeError("Restored database does not match verified backup")
        target.close(); target=None
        os.link(temporary,destination)
        return {"restored":True,"destination_file":destination.name,"integrity_check":restored["integrity_check"],"foreign_key_violations":restored["foreign_key_violations"],"schema_sha256":restored["schema_sha256"],"table_count":len(restored["tables"]),"total_rows":sum(restored["tables"].values()),"sequence_counters":restored["sequence_counters"]}
    finally:
        if target is not None: target.close()
        source.close()
        if temporary is not None: temporary.unlink(missing_ok=True)

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup",required=True); parser.add_argument("--manifest",required=True); parser.add_argument("--output",required=True)
    args=parser.parse_args()
    print(json.dumps(restore_database(args.backup,args.manifest,args.output),ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
