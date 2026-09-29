-- Creates a small made-up source table so main.py can be tried in a
-- scratch Snowflake schema. Uses the same names as data/sample_creditors.csv.
CREATE OR REPLACE TABLE DEMO_DB.PUBLIC.RAW_CREDITORS (
    COMPANY VARCHAR,
    _FIVETRAN_DELETED BOOLEAN DEFAULT FALSE
);

INSERT INTO DEMO_DB.PUBLIC.RAW_CREDITORS (COMPANY)
SELECT column1
FROM VALUES
    ('01 Aster Bank'), ('ASTER BK N.A.'), ('Aster Bank, N.A.'), ('Aster Savings'),
    ('Aster Bank Auto Finance'), ('Aster Bank/Retail'),
    ('Blue Harbor Credit Inc'), ('BHC Card Svcs'), ('Blue Harbour Credit'),
    ('Cedar Fin'), ('Cedar Financial LLC Installment Loan'),
    ('Northwind Capital'), ('Riverstone Collections DBA NWC'), ('Northwind Capitol'),
    ('Orion Funding'), ('07 Orion Funding LLC'), ('Orion Fundng'), ('Orion Funding Group'),
    ('Riverbend Lending'), ('Riverbend Lending [IA]'), ('Riverbend Lendng'), ('River Bend Lending'),
    ('Northstar Services'), ('Green Dot Bank'), ('Green Lawn Fertilizing'), ('Kestrel Medical Billing');
