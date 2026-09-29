-- Optional fictional source table for trying main.py in a disposable schema.
-- Run this only in a Snowflake database and schema you control.
CREATE OR REPLACE TABLE DEMO_DB.PUBLIC.RAW_CREDITORS (
    COMPANY VARCHAR,
    _FIVETRAN_DELETED BOOLEAN DEFAULT FALSE
);

INSERT INTO DEMO_DB.PUBLIC.RAW_CREDITORS (COMPANY, _FIVETRAN_DELETED)
SELECT column1, FALSE
FROM VALUES
    ('ASTER BK'),
    ('Aster Bank LLC'),
    ('Aster Banc'),
    ('Blue Harbor Credit'),
    ('BLUE HARBOR CR'),
    ('Cedar Finance'),
    ('Orion Funding'),
    ('Orion Fundng'),
    ('Riverbend Lending'),
    ('Riverbend Lendng');
