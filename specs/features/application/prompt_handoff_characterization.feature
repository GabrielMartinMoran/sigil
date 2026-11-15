Feature: Characterize persisted prompt handoff
  Before changing durable acceptance, establish how the current library and storage map prompts to turns.

  @integration
  Scenario: A normal initial prompt survives storage and reopening
    Given an agent executes an initial prompt containing text and an image
    When the resulting history is saved and reopened
    Then the persisted prompt retains its content and image
    And its turn and rollback checkpoint associations can be inspected

  @integration
  Scenario: A previously stored trailing prompt is handed to the agent
    Given a session has a stored trailing user request with text and an image
    When the agent continues from that request and the history is saved and reopened
    Then the request and response identifiers and turn associations are recorded
    And rollback checkpoint associations and prompt contents can be compared before and after the handoff
