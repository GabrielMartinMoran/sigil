Feature: Independent background sessions in one service
  Each client selects its own session while all resident sessions may execute.

  @integration
  Scenario: Two clients select different sessions
    Given two authenticated clients observe the same service
    When the first client creates another session
    Then only its selection changes
    And actions from each client target its selected session

  @integration
  Scenario: Independent turns share durable storage without sharing execution
    Given two resident sessions have accepted inputs
    When both consumers start a turn
    Then both turns can progress before either finishes
    And each consumer claims only its own session's rows
    And a consumer failure interrupts only its own session's rows

  @integration
  Scenario: A stream filters live delivery and replay
    Given events belong to two resident sessions
    When a client subscribes with one session ID
    Then replay and live delivery include only its session and service-wide events
    And the global event sequence is retained

  @contract
  Scenario: Session operations cannot silently select another conversation
    Given an authenticated client sends a session operation
    When its session selector is missing
    Then the operation is rejected
    When its selector names a session the service does not hold
    Then the operation is rejected instead of acting on another conversation

  @integration
  Scenario: A background report wakes the originating session
    Given a client has switched away from a session with an asynchronous child
    When the child's report is ready without a root turn running
    Then the original session consumes the report in a new turn
    And the selected session's queue and transcript remain unchanged

  @integration
  Scenario: Idle release preserves work and other owners
    Given resident sessions share the service database and process lock
    When one session exceeds its idle grace
    Then it is released only without attached clients, queued work, active turns, running children, or unread reports
    And another resident session remains usable
    And the process lock and database stay open

  @integration
  Scenario: Service shutdown awaits all resident owners
    Given multiple resident sessions own execution resources
    When the service shuts down
    Then it awaits their consumers, children, and MCP startup and cleanup
    And it attempts remaining cleanup even when one operation fails
    And only then releases process ownership
