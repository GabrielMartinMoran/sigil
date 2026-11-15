Feature: Effective session agent profiles
  A session uses the profile that governs its prompt and permissions.
  Clients identify and select that effective profile using service-owned data.

  @integration
  Scenario: Restoring a saved profile restores its effective behavior
    Given a composition currently uses a different profile
    And a saved session names an installed selectable profile
    When the composition binds the saved session
    Then the registry and execution tree use the saved profile
    And the next request uses its prompt and permissions
    And the session's explicit model override is preserved

  @integration
  Scenario Outline: Invalid saved profiles are replaced durably
    Given a saved session names a <profile>
    And a valid selectable default is installed
    When the composition binds the saved session
    Then the effective profile is the valid default
    And reopening the stored session retains the replacement
    And the execution tree agrees with the effective profile
    Examples:
      | profile         |
      | removed profile |
      | subagent        |

  @unit
  Scenario: A missing default falls back only to selectable profiles
    Given wizard is not a selectable installed profile
    When a default profile is required
    Then the first selectable profile is used if one exists
    And no profile is selected when no selectable profiles exist

  @integration
  Scenario: Restored restrictions govern concrete tool calls
    Given a saved restricted profile denies patch_file for src
    And it permits patch_file within an authorized temporary directory
    When the session is restored and concrete calls pass through the agent capability
    Then patch_file targeting src is denied without writing
    And the authorized call succeeds

  @integration
  Scenario: Service catalog includes global and session project profiles
    Given isolated global and project profile roots contain selectable agents
    When a service opens a session in that project
    Then its catalog contains the global and project agents
    And a same-name project profile overrides the global profile
    When the session workspace changes to another project
    Then project profiles refresh and global profiles remain available

  @integration
  Scenario: Attached terminal uses authoritative profile metadata
    Given the client and server have different same-name profile permissions
    And the server has a selectable profile absent from the client filesystem
    When the user opens the terminal profile picker
    Then the current marker identifies the server's effective profile
    And the server-only profile can be selected
    And permission descriptions come from the server

  @integration
  Scenario: Selecting a profile refreshes effective state
    Given the session is not executing a turn
    When a client selects a server-offered root profile
    Then the server stores and activates that choice
    And connected clients refresh their effective profile
    And the next turn uses the selected profile without restarting

  @integration
  Scenario: Busy and failed selections do not claim success
    When a selection is refused because a turn is running or the profile is invalid
    Then the client retains the previous effective profile
    And reports the refusal

  @unit
  Scenario: Web identification excludes permissions and subagents
    Given the service projects profiles with roles and permission summaries
    When web selector presentation data is prepared
    Then only role agent profiles are offered
    And each option identifies its name, description and origin
    And no permission summary is included in the displayed text

  @contract
  Scenario: Profile authority is documented as current behavior
    When the delivered profile contract is documented
    Then documentation distinguishes current profile authority from future multi-session proposals
    And references the implemented source and published feature contract
