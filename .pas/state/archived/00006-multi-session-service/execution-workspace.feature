Feature: Execution workspace isolation
  Each execution resolves workspace-relative operations against its selected root.
  Selecting a workspace does not change the process working directory.
  This boundary does not yet provide independent session runtimes or report routing.

  @unit
  Scenario: Unbound operations preserve standalone defaults
    Given no execution workspace is bound
    When the workspace root is requested without a composition fallback
    Then the process working directory is used
    And a supplied composition fallback is used instead of the process directory

  @unit
  Scenario: Nested execution bindings restore previous roots
    Given an outer execution has selected a workspace
    When an inner workspace binding finishes or raises
    Then the previous execution root is restored
    And leaving the outer binding restores the unbound default

  @unit
  Scenario: Explicit path roots remain authoritative
    Given an execution workspace is bound
    When a path operation supplies its own explicit root
    Then that explicit root is used rather than reinterpreted as a fallback

  @integration
  Scenario: Concurrent executions retain distinct workspace roots
    Given two executions select different temporary workspace roots
    And neither changes the process working directory
    When both resolve workspace-relative paths concurrently
    Then each observes only its selected root for those operations
    And a synchronous tool dispatched to a worker thread retains the selected root

  @integration
  Scenario: Child tasks inherit the spawning execution root
    Given a child task is created inside an execution workspace binding
    When the parent leaves its binding and another root becomes selected
    Then the child still observes its spawning execution root
    And this does not claim isolated model or report routing

  @integration
  Scenario: Registered tools use execution-aware root callbacks
    Given a composition was constructed for a different fallback workspace
    When its file, shell, image and permission boundaries execute under a bound root
    Then workspace-relative operations use the bound root
    And file conflict checks use the same root
    And shell subprocess cwd follows that root without moving process cwd

  @integration
  Scenario: Server and standalone terminal bind complete agent executions
    Given a server or standalone terminal turn selects a workspace
    When its agent runs tools and then completes, fails or is cancelled
    Then the tools observe that turn's selected root
    And execution workspace binding is reset when the run exits

  @integration
  Scenario: Workspace selection rebinds components without changing process cwd
    Given a composition manages a workspace different from the launch directory
    When another workspace is selected successfully
    Then its workspace components and registries are rebound
    And process cwd remains the launch directory
    And rejected workspace changes leave the selected workspace intact

  @integration
  Scenario: Initial project skill discovery follows the composition workspace
    Given the composition root differs from process cwd
    When default skill discovery first runs
    Then project skills are discovered from the composition root
    And explicit skill-directory overrides remain respected
    And a skill script still executes from its own skill directory

  @contract
  Scenario: Documentation distinguishes workspace selection from process cwd
    When the delivered workspace contract is documented
    Then it identifies execution snapshots and unbound fallback
    And documents intentional explicit subprocess directory exceptions
    And does not claim concurrent session runtimes are already delivered
