DROP TRIGGER IF EXISTS trg_profile_scoped_data_touch ON profile_scoped_data;
DROP FUNCTION IF EXISTS profile_scoped_data_touch_updated_at();
DROP TABLE IF EXISTS profile_scoped_data;

DELETE FROM private_schema_version WHERE version = 24;
