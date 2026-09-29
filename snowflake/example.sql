-- Example integration after loading the generated CSV files into a demo schema.
-- Replace DEMO_DB.PUBLIC names with tables in an account you control.
-- This example has not been run against a Snowflake account.

-- The alias map has one row per distinct source name. Join at that grain.
SELECT
    a.CANONICAL_NAME,
    COUNT(*) AS SOURCE_ROWS
FROM DEMO_DB.PUBLIC.RAW_CREDITORS AS r
JOIN DEMO_DB.PUBLIC.CREDITOR_ALIAS_MAP AS a
    ON r.RAW_NAME = a.RAW_NAME
GROUP BY a.CANONICAL_NAME
ORDER BY SOURCE_ROWS DESC;

-- Check that every nonblank input name has a mapping.
SELECT COUNT(*) AS UNMAPPED_ROWS
FROM DEMO_DB.PUBLIC.RAW_CREDITORS AS r
LEFT JOIN DEMO_DB.PUBLIC.CREDITOR_ALIAS_MAP AS a
    ON r.RAW_NAME = a.RAW_NAME
WHERE r.RAW_NAME IS NOT NULL
  AND TRIM(r.RAW_NAME) <> ''
  AND a.RAW_NAME IS NULL;
