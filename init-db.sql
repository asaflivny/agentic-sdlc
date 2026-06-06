-- Create dev user
CREATE ROLE dev WITH LOGIN PASSWORD 'dev-password';

-- Create databases
CREATE DATABASE inventory OWNER dev;
CREATE DATABASE gitea OWNER dev;
CREATE DATABASE pgadmin OWNER dev;

-- Grant privileges
ALTER ROLE dev CREATEDB;
GRANT ALL PRIVILEGES ON DATABASE inventory TO dev;
GRANT ALL PRIVILEGES ON DATABASE gitea TO dev;
GRANT ALL PRIVILEGES ON DATABASE pgadmin TO dev;
