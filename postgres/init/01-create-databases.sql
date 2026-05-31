-- Create users
CREATE USER moodle_user WITH PASSWORD 'moodle_password';
CREATE USER jupyterhub_user WITH PASSWORD 'jupyterhub_password';

-- Create databases and assign owners
CREATE DATABASE moodle OWNER moodle_user;
CREATE DATABASE jupyterhub OWNER jupyterhub_user;

-- Grant privileges explicitly
GRANT ALL PRIVILEGES ON DATABASE moodle TO moodle_user;
GRANT ALL PRIVILEGES ON DATABASE jupyterhub TO jupyterhub_user;
