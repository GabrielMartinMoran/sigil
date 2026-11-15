Feature: Single session runtime ownership
  The service facade owns process resources while one session runtime owns execution state.
  This refactor preserves the current single-session behavior and does not enable concurrency.

  @contract
  Scenario: Existing service callers retain their public runtime API
    Given callers import ServerRuntime and its existing public support types
    When the runtime is constructed with the existing arguments
    Then its public methods and properties remain usable with the same signatures
    And composition and hub remain available to existing routes and lifecycle code
    And the facade exposes its distinct session runtime explicitly

  @integration
  Scenario: Execution state belongs to the single session runtime
    Given the facade holds one session runtime
    When queue, turn, steering, question, retry and report-wake behavior runs
    Then the existing behavioral assertions remain satisfied
    And callbacks execute against the session runtime's execution state
    And workspace execution binding remains active through the turn

  @integration
  Scenario: Session cleanup cannot release process resources
    Given the service holds its database ownership lock
    And the session runtime uses the shared cached database
    When the session runtime is closed
    Then the service lock remains held until the facade closes
    And the shared database remains available

  @integration
  Scenario: Facade shutdown preserves graceful queue semantics
    Given accepted work was not executed before graceful shutdown
    When the facade closes and is closed again
    Then ownership is released idempotently
    And never-executed queue rows are not marked interrupted by shutdown
    And session callbacks and pending questions receive their existing cleanup

  @contract
  Scenario: Implementation imports remain acyclic and patch seams remain effective
    When runtime execution moves to its session module
    Then the session module does not import back from the facade
    And existing public runtime support imports still resolve
    And slow-turn and tool-event tests exercise the moved implementation

  @contract
  Scenario: Current ownership documentation preserves delivery boundaries
    When the extracted architecture is documented
    Then process and session resource lifetimes are distinguished
    And concurrent sessions and session-addressed HTTP requests remain future work
