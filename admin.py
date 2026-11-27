"""
Yopiq (parolli) admin panel. Bot bilan bitta jarayonda ishlaydi.
Sahifalar: / (statistika + foydalanuvchilar), /logs (yuborishlar tarixi).
"""
import asyncio
import hmac
import secrets
import sqlite3
from html import escape

from aiohttp import web

COOKIE = "admin_sid"

CSS = """
:root{--bg:#0f1419;--card:#182029;--line:#263241;--text:#e6edf3;--mut:#8b98a5;
--acc:#3b82f6;--ok:#22c55e;--bad:#ef4444;--warn:#f59e0b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}
header{display:flex;align-items:center;gap:20px;padding:14px 24px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header b{font-size:16px}header nav{display:flex;gap:16px;flex:1}
header nav a{color:var(--mut)}header nav a.on{color:var(--text)}
main{padding:24px;max-width:1200px;margin:auto}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.stat span{color:var(--mut);font-size:12px}.stat div{font-size:26px;font-weight:600}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{width:100%;border-collapse:collapse}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:500;font-size:12px;white-space:nowrap}tr:last-child td{border-bottom:0}
.txt{max-width:280px;white-space:pre-wrap;word-break:break-word;color:var(--mut)}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px}
.t-ok{background:#22c55e22;color:var(--ok)}.t-bad{background:#ef444422;color:var(--bad)}.t-mut{background:#8b98a522;color:var(--mut)}
form.i{display:inline}button{background:#263241;color:var(--text);border:1px solid var(--line);
border-radius:6px;padding:4px 10px;cursor:pointer;font:inherit;font-size:12px;margin:2px}
button:hover{border-color:var(--acc)}button.red{color:var(--bad)}
.login{max-width:340px;margin:12vh auto;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px}
.login input{width:100%;padding:10px;margin:12px 0;background:var(--bg);border:1px solid var(--line);border-radius:8px;color:var(--text);font:inherit}
.login button{width:100%;padding:10px;background:var(--acc);border:0;font-size:14px}
.err{color:var(--bad)}h2{font-size:15px;margin:0 0 12px}
"""


def page(title: str, body: str, nav: str = "") -> web.Response:
    links = ""
    if nav:
        links = "".join(
            f'<a href="{h}" class="{"on" if nav == h else ""}">{t}</a>'
            for h, t in [("/", "Foydalanuvchilar"), ("/logs", "Yuborishlar")]
        )
        links = (f'<header><b>🛡 Admin panel</b><nav>{links}</nav>'
                 f'<form class="i" method="post" action="/logout"><button>Chiqish</button></form></header>')
    html = (f"<!doctype html><html lang='uz'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title><style>{CSS}</style></head>"
            f"<body>{links}<main>{body}</main></body></html>")
    return web.Response(text=html, content_type="text/html")


def create_app(db: sqlite3.Connection, password: str, force_logout) -> web.Application:
    sessions: set[str] = set()

    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path == "/health":  # Render va keep-alive uchun
            return web.Response(text="ok")
        if request.path in ("/login",):
            return await handler(request)
        if request.cookies.get(COOKIE) not in sessions:
            raise web.HTTPFound("/login")
        return await handler(request)

    app = web.Application(middlewares=[auth])
    routes = web.RouteTableDef()

    # ------------------------------------------------ login / logout
    @routes.get("/login")
    async def login_form(request, error=""):
        err = f"<p class='err'>{error}</p>" if error else ""
        return page("Kirish", f"""<form class="login" method="post">
            <h2>🛡 Admin panel</h2>{err}
            <input type="password" name="password" placeholder="Parol" autofocus required>
            <button>Kirish</button></form>""")

    @routes.post("/login")
    async def login(request):
        data = await request.post()
        if hmac.compare_digest(str(data.get("password", "")), password):
            sid = secrets.token_urlsafe(32)
            sessions.add(sid)
            resp = web.HTTPFound("/")
            resp.set_cookie(COOKIE, sid, httponly=True, samesite="Strict", max_age=7 * 86400)
            raise resp
        await asyncio.sleep(1.5)  # parol terib ko'rishni sekinlatish
        return await login_form(request, "Parol noto'g'ri")

    @routes.post("/logout")
    async def logout(request):
        sessions.discard(request.cookies.get(COOKIE))
        resp = web.HTTPFound("/login")
        resp.del_cookie(COOKIE)
        raise resp

    # ------------------------------------------------ bosh sahifa
    @routes.get("/")
    async def index(request):
        q = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
        stats = [
            ("Foydalanuvchilar", q("SELECT COUNT(*) FROM users")),
            ("Akkaunt ulangan", q("SELECT COUNT(*) FROM users WHERE session IS NOT NULL")),
            ("Bloklangan", q("SELECT COUNT(*) FROM users WHERE blocked=1")),
            ("Bugun yuborildi", q("SELECT COUNT(*) FROM sends WHERE ok=1 AND date(ts)=date('now','localtime')")),
            ("Jami yuborildi", q("SELECT COUNT(*) FROM sends WHERE ok=1")),
            ("Xatolar", q("SELECT COUNT(*) FROM sends WHERE ok=0")),
        ]
        cards = "".join(f"<div class='stat'><span>{t}</span><div>{v}</div></div>" for t, v in stats)

        rows = db.execute("""
            SELECT u.user_id, u.name, u.username, u.session IS NOT NULL, u.text,
                   COALESCE(u.blocked,0), u.created,
                   (SELECT COUNT(*) FROM sends s WHERE s.user_id=u.user_id AND s.ok=1),
                   (SELECT COUNT(*) FROM sends s WHERE s.user_id=u.user_id AND s.ok=0),
                   (SELECT MAX(ts) FROM sends s WHERE s.user_id=u.user_id)
            FROM users u ORDER BY u.created DESC""").fetchall()

        trs = ""
        for uid, name, uname, connected, text, blocked, created, ok, bad, last in rows:
            who = escape(name or "—") + (f"<br><a href='https://t.me/{escape(uname)}' target='_blank'>@{escape(uname)}</a>" if uname else "")
            status = ("<span class='tag t-ok'>ulangan</span>" if connected
                      else "<span class='tag t-mut'>ulanmagan</span>")
            if blocked:
                status += " <span class='tag t-bad'>bloklangan</span>"
            btn = lambda act, label, cls="": (  # noqa: E731
                f"<form class='i' method='post' action='/user/{uid}/{act}'"
                f" onsubmit=\"return confirm('{label}?')\"><button class='{cls}'>{label}</button></form>")
            actions = (btn("unblock", "Blokdan chiqarish") if blocked else btn("block", "Bloklash", "red"))
            if connected:
                actions += btn("disconnect", "Akkauntni uzish", "red")
            if text:
                actions += btn("deltext", "Matnni o'chirish")
            trs += (f"<tr><td>{who}<br><small style='color:var(--mut)'>{uid}</small></td>"
                    f"<td>{status}</td><td class='txt'>{escape(text or '—')}</td>"
                    f"<td>{ok} <span style='color:var(--mut)'>/ {bad} xato</span></td>"
                    f"<td><small>{escape(last or '—')}<br>ro'yxat: {escape(created or '—')}</small></td>"
                    f"<td>{actions}</td></tr>")
        if not trs:
            trs = "<tr><td colspan=6 style='color:var(--mut)'>Hali foydalanuvchi yo'q</td></tr>"

        return page("Admin panel", f"""<div class="stats">{cards}</div>
            <h2>Foydalanuvchilar</h2><div class="box"><table>
            <tr><th>Foydalanuvchi</th><th>Holat</th><th>Matn</th><th>Yuborgan</th><th>Oxirgi faollik</th><th>Amallar</th></tr>
            {trs}</table></div>""", nav="/")

    # ------------------------------------------------ amallar
    @routes.post("/user/{uid:\\d+}/{action}")
    async def user_action(request):
        uid = int(request.match_info["uid"])
        action = request.match_info["action"]
        if action == "block":
            db.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
        elif action == "unblock":
            db.execute("UPDATE users SET blocked=0 WHERE user_id=?", (uid,))
        elif action == "deltext":
            db.execute("UPDATE users SET text=NULL WHERE user_id=?", (uid,))
        elif action == "disconnect":
            await force_logout(uid)
        else:
            raise web.HTTPBadRequest()
        db.commit()
        raise web.HTTPFound("/")

    # ------------------------------------------------ yuborishlar tarixi
    @routes.get("/logs")
    async def logs(request):
        rows = db.execute("""
            SELECT s.ts, s.user_id, u.name, u.username, s.target, s.ok, s.error
            FROM sends s LEFT JOIN users u ON u.user_id=s.user_id
            ORDER BY s.id DESC LIMIT 500""").fetchall()
        trs = "".join(
            f"<tr><td><small>{escape(ts)}</small></td>"
            f"<td>{escape(name or str(uid))}{(' <small>@' + escape(un) + '</small>') if un else ''}</td>"
            f"<td><a href='https://t.me/{escape(t)}' target='_blank'>@{escape(t)}</a></td>"
            f"<td>{'<span class=\"tag t-ok\">yuborildi</span>' if ok else '<span class=\"tag t-bad\">' + escape(err or 'xato') + '</span>'}</td></tr>"
            for ts, uid, name, un, t, ok, err in rows
        ) or "<tr><td colspan=4 style='color:var(--mut)'>Hali yuborish yo'q</td></tr>"
        return page("Yuborishlar", f"""<h2>Oxirgi 500 ta yuborish</h2><div class="box"><table>
            <tr><th>Vaqt</th><th>Kim</th><th>Kimga</th><th>Natija</th></tr>{trs}</table></div>""", nav="/logs")

    app.add_routes(routes)
    return app
