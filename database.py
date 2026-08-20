import mysql.connector
import os
import base64
from typing import Optional

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
}

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Komponenter-tabell
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS components (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            category VARCHAR(100),
            quantity INT DEFAULT 0,
            location VARCHAR(100),
            description TEXT,
            specs TEXT,
            image LONGBLOB,
            image_content_type VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)

    # Legg til specs-kolonne hvis den ikke finnes (for eksisterende databaser)
    try:
        cursor.execute("ALTER TABLE components ADD COLUMN specs TEXT")
    except:
        pass

    # Logg-tabell
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            component_id INT,
            component_name VARCHAR(255),
            action VARCHAR(50),
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()

def log_action(component_id: int, component_name: str, action: str, details: str = ""):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO activity_log (component_id, component_name, action, details)
            VALUES (%s, %s, %s, %s)
        """, (component_id, component_name, action, details))
        conn.commit()
        cursor.close()
        conn.close()
    except:
        pass

def get_activity_log(limit: int = 50):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, component_id, component_name, action, details, created_at
        FROM activity_log
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        if row["created_at"]:
            row["created_at"] = str(row["created_at"])
    return rows

def get_all_components():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, name, category, quantity, location, description, specs,
               image_content_type, created_at, updated_at
        FROM components ORDER BY category, name
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        if row["created_at"]: row["created_at"] = str(row["created_at"])
        if row["updated_at"]: row["updated_at"] = str(row["updated_at"])
    return rows

def get_component_by_id(component_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, name, category, quantity, location, description, specs,
               image, image_content_type, created_at, updated_at
        FROM components WHERE id = %s
    """, (component_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return None
    if row["image"]:
        row["image"] = base64.b64encode(row["image"]).decode("utf-8")
    if row["created_at"]: row["created_at"] = str(row["created_at"])
    if row["updated_at"]: row["updated_at"] = str(row["updated_at"])
    return row

def add_component(name, category, quantity, location="", description="", specs="",
                  image_data=None, image_content_type=None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO components (name, category, quantity, location, description, specs, image, image_content_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (name, category, quantity, location, description, specs, image_data, image_content_type))
    conn.commit()
    component_id = cursor.lastrowid
    cursor.close()
    conn.close()
    log_action(component_id, name, "lagt_til", f"Antall: {quantity}, Lokasjon: {location}")
    return component_id

def update_component(component_id: int, name: str, category: str, quantity: int,
                     location: str, description: str, specs: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE components SET name=%s, category=%s, quantity=%s,
        location=%s, description=%s, specs=%s WHERE id=%s
    """, (name, category, quantity, location, description, specs, component_id))
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()
    if affected:
        log_action(component_id, name, "redigert", f"Antall: {quantity}, Lokasjon: {location}")
    return affected > 0

def update_component_quantity(component_id: int, quantity: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT name, quantity FROM components WHERE id = %s", (component_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        return False
    cursor.execute("UPDATE components SET quantity = %s WHERE id = %s", (quantity, component_id))
    conn.commit()
    cursor.close()
    conn.close()
    log_action(component_id, row["name"], "antall_endret", f"Nytt antall: {quantity}")
    return True

def delete_component(component_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT name FROM components WHERE id = %s", (component_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        return False
    cursor.execute("DELETE FROM components WHERE id = %s", (component_id,))
    conn.commit()
    cursor.close()
    conn.close()
    log_action(component_id, row["name"], "slettet", "")
    return True
