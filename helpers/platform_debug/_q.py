import psycopg2, sys
c = psycopg2.connect(host='127.0.0.1', port=56729, user='postgres', dbname='postgres')
cur = c.cursor()
for sql in sys.argv[1:]:
    try:
        cur.execute(sql)
        for r in cur.fetchall():
            print(r)
    except Exception as e:
        c.rollback()
        print('ERR:', e)
