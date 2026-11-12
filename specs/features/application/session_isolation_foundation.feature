Feature: Session isolation foundation
  Fixed session compositions use their own project roots and shell process ownership.

  @integration
  Scenario: Internal file and permission checks use the composition project root
    Given two fixed session compositions have different project roots
    And the process working directory is outside both roots
    When each composition reads, writes, or checks access to a relative project path
    Then each operation uses its own project root
    And the process working directory remains unchanged

  @integration
  Scenario: Shell commands use the composition project root
    Given two fixed session compositions have different project roots
    When each composition starts an internal shell command
    Then each command starts in its own project root
    And neither command changes the process working directory

  @integration
  Scenario: Cancelling shell processes does not affect another composition
    Given two fixed session compositions each own a running internal shell command
    When the first composition cancels its shell work
    Then its command and process group are stopped
    And the second composition's command continues

  @integration
  Scenario: Cancellation covers shell process admission in progress
    Given a shell command is being started by a fixed session composition
    When cancellation races with process creation and registration
    Then no admitted command remains running after cancellation completes
    And work owned by another composition is unaffected

  @integration
  Scenario: Switching projects refreshes project-specific agent profiles
    Given a composition uses a project with a project-specific agent profile
    And a different project has an agent profile with the same name
    When the composition switches to the different project without active work
    Then subsequent agent creation uses the destination project's profile
    And cached agents from the previous project are not reused
    And an active agent unavailable in the destination falls back to an available agent
