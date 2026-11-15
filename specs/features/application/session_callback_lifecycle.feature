Feature: Session callback lifecycle
  A session runtime disconnects its own callback registrations on close
  without taking ownership of service resources or unrelated tool sinks.

  @integration
  Scenario: Closing a session disconnects every registered callback
    Given a session runtime owns one composition
    And all seven session callback registrations are active
    When the session runtime closes
    Then its question asker and tool event sink are detached
    And its extension notice and model retry callbacks are unset
    And its model busy predicate returns false
    And its tree change and idle callbacks are unset
    And the composition tree relay remains available

  @integration
  Scenario: Repeated session close preserves other owners and process resources
    Given a session runtime owns one composition
    And an unrelated tool sink is registered
    And the service holds the database ownership lock
    When the session runtime closes twice
    Then its callback registrations remain detached or neutral
    And the unrelated tool sink remains registered
    And the service ownership lock remains held
    And the shared database remains usable
