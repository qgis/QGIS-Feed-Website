-- Installed into template1 so every database created afterwards inherits it,
-- including the throwaway database the Django test runner clones.
\c template1
CREATE EXTENSION IF NOT EXISTS postgis;
