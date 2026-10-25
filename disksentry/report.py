"""CLI 报告：把指定时间窗内的增长情况打印到终端。"""
import time


def _fmt_size(n):
    a = abs(n)
    sign = "-" if n < 0 else "+"
    if a >= 1024 ** 3:
        return f"{sign}{a / 1024 ** 3:.2f} GB"
    if a >= 1024 ** 2:
        return f"{sign}{a / 1024 ** 2:.1f} MB"
    if a >= 1024:
        return f"{sign}{a / 1024:.0f} KB"
    return f"{sign}{a} B"


def render_report(conn, hours: int, top: int = 15) -> str:
    from . import store
    since = time.time() - hours * 3600
    lines = [f"== 近 {hours} 小时磁盘增长报告 =="]

    deltas = store.window_deltas(conn, since)
    if not deltas:
        lines.append("（该时间窗内没有变更记录）")
        return "\n".join(lines)

    lines.append("")
    lines.append("-- 各监控根净变化 --")
    for root, d in sorted(deltas.items(), key=lambda x: -abs(x[1])):
        lines.append(f"  {root:<28} {_fmt_size(d):>12}")

    dirs, files = store.top_dirs(conn, since, topn=top)
    if dirs:
        lines.append("")
        lines.append("-- 增长最快的目录 --")
        for item in dirs:
            lines.append(f"  {_fmt_size(item['delta']):>12}  {item['path']}")
    if files:
        lines.append("")
        lines.append("-- 增长最快的文件 --")
        for item in files:
            lines.append(f"  {_fmt_size(item['delta']):>12}  {item['path']}")

    lines.append("")
    lines.append("-- 最近变更 --")
    for row in store.recent_changes(conn, since, order="ts", limit=top):
        t = time.strftime("%m-%d %H:%M", time.localtime(row["ts"]))
        kind = {"new": "新建", "grow": "增长", "shrink": "缩小", "delete": "删除"}[row["kind"]]
        lines.append(f"  {t}  [{kind}] {_fmt_size(row['delta']):>12}  {row['path']}")
    return "\n".join(lines)
