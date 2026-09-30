import sqlite3, sys

c = sqlite3.connect("data/app.db")
for i in map(int, sys.argv[1:]):
    c.execute("delete from people where id=?", (i,))
    c.execute("delete from pairs where a=? or b=?", (i, i))
    c.execute("delete from dates where a=? or b=?", (i, i))
c.commit()
print("left:", c.execute("select count(*) from people").fetchone()[0])