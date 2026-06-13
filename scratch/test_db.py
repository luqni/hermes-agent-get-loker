import psycopg2

try:
    conn = psycopg2.connect(
        dbname="jobline",
            user="postgres",
            password="P@ssw0rd",
            host="127.0.0.1",
            port="5432"
    )
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM platforms;")
    rows = cursor.fetchall()
    print("Platforms in DB:")
    for row in rows:
        print(f"ID: {row[0]}, Name: {row[1]}")
    
    # Also check a few recent job listings to see their platform_id and source_url
    cursor.execute("SELECT platform_id, source_url FROM job_listings ORDER BY created_at DESC LIMIT 5;")
    jobs = cursor.fetchall()
    print("\nRecent Job Listings:")
    for job in jobs:
        print(f"Platform ID: {job[0]}, URL: {job[1]}")
        
    cursor.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
