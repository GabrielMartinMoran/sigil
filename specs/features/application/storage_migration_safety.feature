Feature: Safe storage initialization and migrations
  Existing session databases must be preserved while schema changes are applied.

  @integration
  Scenario: A failed migration leaves the previous database unchanged
    Given a coherent older database with session data
    When schema initialization fails after a migration statement
    Then the previous schema version and session data remain intact
    And reopening can retry the migration

  @integration
  Scenario: Concurrent processes migrate the same database
    Given a coherent older database with session data
    When two processes open it concurrently
    Then both observe the current schema after successful initialization
    And the existing data remains intact

  @integration
  Scenario: A newer database is not downgraded
    Given a database has a schema version newer than this Sigil version
    When Sigil attempts to open it
    Then initialization reports an incompatible schema
    And its schema version is not reduced

  @integration
  Scenario: Storage requests full synchronization for committed writes
    Given Sigil opens a supported local database
    Then its SQLite connection uses WAL and full synchronous mode
