# -*- coding: utf-8 -*-
"""
统一存储层：sqlite（panorama.db）是唯一后端，事务原子写，上一版存 *_bak 表。
JSON/CSV 文件侧已降级为「导出备份」：run_daily.sh 每日全量导出一份，供肉眼查看、
手工应急修改（改完 pull 回库）、以及数据库损坏时恢复。**新功能一律只考虑 sqlite。**

数据集名 = 原文件名去扩展名（cashflow.json → "cashflow"，history.csv → "history"）。
DOCS 是 JSON 文档；TABLES 是行表，语义与 csv.DictReader 一致：**读出的值全是字符串**。

  python3 storage.py status        当前后端 + 各数据集概况
  python3 storage.py export        数据库 → 文件，全量导出备份（不切换后端）
  python3 storage.py push <数据集>  数据库 → 文件（想手工改某个 CSV 时先导出）
  python3 storage.py pull <数据集>  文件 → 数据库（手工改完拉回）
  python3 storage.py to-file --force  切回 file 后端（仅数据库损坏时的应急通道）
"""
import csv
import datetime
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "panorama.db"
CONFIG = BASE / "storage.json"

# 注册表：迁移/status 的范围。load/save 不限于注册表（按名即可读写）。
DOCS = ["cashflow", "subscriptions", "insurance", "manual_values", "passthrough",
        "loans", "goal", "latest_snapshot", "quotes_cache", "klines_cache"]
TABLES = ["holdings", "accounts", "holdings_history", "insurance_cashvalue",
          "history", "history_full", "cashflow_history"]

# 测试钩子：强制后端 / 重定向某数据集的文件路径（仅 file 后端生效）
_FORCE_BACKEND = None
PATH_OVERRIDE = {}

# 演示模式：数据根切到 demo/(虚构人物「张小满」一家)，后端强制走文件——
# 零侵入:主逻辑一个 if 都不用加,只是换了读写的目录。
# 开启:环境变量 PANORAMA_DEMO=1，或任意脚本前 `import storage; storage.enable_demo()`
DATA_ROOT = BASE
DEMO = False


def enable_demo():
    global DATA_ROOT, DEMO, _FORCE_BACKEND
    DATA_ROOT = BASE / "demo"
    DEMO = True
    _FORCE_BACKEND = "file"          # 演示数据就是那些文件，不进 sqlite


if os.environ.get("PANORAMA_DEMO") == "1":
    enable_demo()


def backend():
    if _FORCE_BACKEND:
        return _FORCE_BACKEND
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("backend", "sqlite")
    except (OSError, json.JSONDecodeError):
        # 配置丢失时宁可读库也不读可能过期的文件备份
        return "sqlite" if DB_PATH.exists() else "file"


def _set_backend(bk):
    CONFIG.write_text(json.dumps({"backend": bk}, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _name(name):
    """允许带扩展名调用（read_csv("history.csv")）——统一去掉。"""
    for ext in (".json", ".csv"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _doc_path(name):
    return PATH_OVERRIDE.get(name) or (DATA_ROOT / f"{name}.json")


def _table_path(name):
    return PATH_OVERRIDE.get(name) or (DATA_ROOT / f"{name}.csv")


def _bak_path(p):
    """xxx.json → xxx.bak.json；xxx.csv → xxx.bak.csv（沿用项目双备份命名）。"""
    return p.with_name(p.stem + ".bak" + p.suffix)


# ───────────────────────── sqlite 后端 ─────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(name TEXT PRIMARY KEY, payload TEXT NOT NULL, updated TEXT);
CREATE TABLE IF NOT EXISTS docs_bak(name TEXT PRIMARY KEY, payload TEXT NOT NULL, updated TEXT);
CREATE TABLE IF NOT EXISTS tables_(name TEXT PRIMARY KEY, fields TEXT NOT NULL,
                                   payload TEXT NOT NULL, updated TEXT);
CREATE TABLE IF NOT EXISTS tables_bak(name TEXT PRIMARY KEY, fields TEXT NOT NULL,
                                      payload TEXT NOT NULL, updated TEXT);
"""


def _db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(_SCHEMA)
    return con


def _db_get(con, table, name):
    cur = con.execute(f"SELECT fields, payload FROM {table} WHERE name=?", (name,)) \
        if table.startswith("tables") else \
        con.execute(f"SELECT NULL, payload FROM {table} WHERE name=?", (name,))
    return cur.fetchone()


def _db_put(con, kind, name, fields, payload, backup):
    tbl, bak = ("tables_", "tables_bak") if kind == "table" else ("docs", "docs_bak")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    if backup:
        cur = _db_get(con, tbl, name)
        if cur:
            if kind == "table":
                con.execute(f"INSERT OR REPLACE INTO {bak} VALUES(?,?,?,?)",
                            (name, cur[0], cur[1], now))
            else:
                con.execute(f"INSERT OR REPLACE INTO {bak} VALUES(?,?,?)",
                            (name, cur[1], now))
    if kind == "table":
        con.execute(f"INSERT OR REPLACE INTO {tbl} VALUES(?,?,?,?)",
                    (name, fields, payload, now))
    else:
        con.execute(f"INSERT OR REPLACE INTO {tbl} VALUES(?,?,?)", (name, payload, now))
    con.commit()


# ───────────────────────── 公共 API ─────────────────────────
def load_doc(name, default=None):
    name = _name(name)
    if backend() == "sqlite":
        with closing(_db()) as con:
            row = _db_get(con, "docs", name)
        return json.loads(row[1]) if row else ({} if default is None else default)
    p = _doc_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() \
        else ({} if default is None else default)


def save_doc(name, obj, backup=True):
    name = _name(name)
    payload = json.dumps(obj, ensure_ascii=False, indent=2)
    if backend() == "sqlite":
        with closing(_db()) as con:
            _db_put(con, "doc", name, None, payload, backup)
        return
    p = _doc_path(name)
    if backup and p.exists():
        _bak_path(p).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(payload, encoding="utf-8")


def doc_exists(name):
    name = _name(name)
    if backend() == "sqlite":
        with closing(_db()) as con:
            return _db_get(con, "docs", name) is not None
    return _doc_path(name).exists()


def _stringify(fields, rows):
    """行值统一转字符串（None→""），与 CSV 写读一轮后的语义一致。"""
    return [{k: ("" if r.get(k) is None else str(r.get(k, ""))) for k in fields}
            for r in rows]


def load_table(name, default=None):
    """→ 行 dict 列表（值均为字符串）。数据集缺失：default=None 时抛 FileNotFoundError。"""
    name = _name(name)
    if backend() == "sqlite":
        with closing(_db()) as con:
            row = _db_get(con, "tables_", name)
        if row is None:
            if default is None:
                raise FileNotFoundError(f"数据集 {name} 不在 {DB_PATH.name}")
            return default
        return json.loads(row[1])
    p = _table_path(name)
    if not p.exists():
        if default is None:
            raise FileNotFoundError(p)
        return default
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_table(name, fields, rows, backup=True):
    name = _name(name)
    rows = _stringify(fields, rows)
    if backend() == "sqlite":
        with closing(_db()) as con:
            _db_put(con, "table", name, json.dumps(list(fields), ensure_ascii=False),
                    json.dumps(rows, ensure_ascii=False), backup)
        return
    p = _table_path(name)
    if backup and p.exists():
        _bak_path(p).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def table_exists(name):
    name = _name(name)
    if backend() == "sqlite":
        with closing(_db()) as con:
            return _db_get(con, "tables_", name) is not None
    return _table_path(name).exists()


# ───────────────────────── 迁移 / CLI ─────────────────────────
def _copy_dataset(name, kind, src, dst):
    """单数据集在两后端间拷贝。返回 (是否拷贝, 概况文字)。"""
    global _FORCE_BACKEND
    saved = _FORCE_BACKEND
    try:
        _FORCE_BACKEND = src
        if kind == "doc":
            if not doc_exists(name):
                return False, "源缺失，跳过"
            obj = load_doc(name)
            _FORCE_BACKEND = dst
            save_doc(name, obj, backup=True)
            return True, "文档"
        if not table_exists(name):
            return False, "源缺失，跳过"
        rows = load_table(name)
        fields = _table_fields(name, src, rows)
        _FORCE_BACKEND = dst
        save_table(name, fields, rows, backup=True)
        return True, f"{len(rows)} 行"
    finally:
        _FORCE_BACKEND = saved


def _table_fields(name, bk, rows):
    """取列序：file 用 CSV 表头，sqlite 用存储的 fields；兜底用首行键序。"""
    if bk == "file":
        p = _table_path(name)
        with p.open(encoding="utf-8") as f:
            return csv.DictReader(f).fieldnames or (list(rows[0]) if rows else [])
    with closing(_db()) as con:
        row = _db_get(con, "tables_", name)
    return json.loads(row[0]) if row else (list(rows[0]) if rows else [])


def _migrate(dst):
    src = "file" if dst == "sqlite" else "sqlite"
    print(f"迁移 {src} → {dst}：")
    for name in DOCS:
        ok, info = _copy_dataset(name, "doc", src, dst)
        print(f"  {'✅' if ok else '⏭️ '} {name}  {info}")
    for name in TABLES:
        ok, info = _copy_dataset(name, "table", src, dst)
        print(f"  {'✅' if ok else '⏭️ '} {name}  {info}")
    _set_backend(dst)
    other = "文件" if dst == "sqlite" else "数据库"
    print(f"后端已切换为 {dst}。注意：此后{other}侧是冻结快照，不再自动同步。")


def _export():
    """数据库 → 文件全量导出（不切换后端）。file 侧仅作备份用。"""
    n = 0
    for name in DOCS:
        ok, _ = _copy_dataset(name, "doc", "sqlite", "file")
        n += ok
    for name in TABLES:
        ok, _ = _copy_dataset(name, "table", "sqlite", "file")
        n += ok
    print(f"✅ 导出备份 {n} 个数据集 → JSON/CSV（.bak 为上一次导出）")


def _status():
    bk = backend()
    print(f"当前后端: {bk}   (配置 {CONFIG.name}；file 侧=导出备份)")
    if DB_PATH.exists():
        print(f"数据库: {DB_PATH.name}  {DB_PATH.stat().st_size/1024:.0f} KB")
    for name in DOCS:
        mark = "●" if doc_exists(name) else "○"
        print(f"  {mark} 文档 {name}")
    for name in TABLES:
        n = len(load_table(name, [])) if table_exists(name) else "-"
        mark = "●" if table_exists(name) else "○"
        print(f"  {mark} 表   {name}  {n} 行" if n != "-" else f"  {mark} 表   {name}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        _status()
    elif cmd == "to-sqlite":
        _migrate("sqlite")
    elif cmd == "export":
        _export()
    elif cmd == "to-file":
        if "--force" not in argv:
            print("⚠️  file 侧已降级为导出备份，不再作为后端使用。\n"
                  "    仅当数据库损坏需要应急回退时：python3 storage.py to-file --force")
        else:
            _migrate("file")
    elif cmd in ("push", "pull") and len(argv) > 2:
        name = _name(argv[2])
        kind = "doc" if name in DOCS else "table"
        src, dst = ("sqlite", "file") if cmd == "push" else ("file", "sqlite")
        ok, info = _copy_dataset(name, kind, src, dst)
        print(f"{'✅' if ok else '⏭️ '} {name} {src}→{dst}  {info}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
