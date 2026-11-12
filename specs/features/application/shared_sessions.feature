Feature: Shared live sessions
  Clients interact with a session owned by a shared background service.

  @integration
  Scenario: Two clients observe the same active session
    Given two clients are connected to the same session
    When either client sends a message
    Then both clients observe the message and subsequent execution updates
    And only one session execution processes the message

  @integration
  Scenario: Work continues without connected clients
    Given a session is executing work
    When all clients disconnect
    Then the work continues in the background
    And a reconnecting client can recover the current session state

  @integration
  Scenario: Accepted messages survive service failure without duplication
    Given a client sends a message with images and receives confirmation
    When the service fails and the client retries the same command
    Then the message and images remain available exactly once in the session
    And any interrupted execution is not automatically repeated

  @integration
  Scenario: Model and effort changes apply at the next step
    Given a session has an active agent step and inheriting subagents
    When a client changes the model and effort
    Then active requests retain their existing settings
    And subsequent steps use the new settings for the root and inheriting subagents
    And subagents with explicitly configured models retain their configuration

  @integration
  Scenario: Agent changes apply to the next turn
    Given a session has an active turn
    When a client selects another agent
    Then the active turn retains its current agent
    And the next turn uses the selected agent

  @integration
  Scenario: Stop affects all work in one session only
    Given two sessions have active work and background subagents
    When a client stops the first session
    Then its active work, commands, and background subagents stop
    And work in the second session continues

  @integration
  Scenario: Project and session context is isolated
    Given sessions belong to different project directories
    When their tools operate concurrently
    Then each tool receives its own session and project context
    And project tools are available only in their project
    And operations do not change the shared service working directory

  @integration
  Scenario: A browser sends images to a shared session
    Given an authenticated browser and a TUI observe the same session
    When the browser sends a message containing valid images
    Then both clients observe the message and its attachments
    And the images remain available when the session is reopened

  @integration
  Scenario: A reconnecting client detects missing updates
    Given a client disconnects while its session changes
    When its event cursor is unavailable or belongs to an earlier service instance
    Then the client reloads an authoritative current state
    And it resumes receiving updates without silently overlooking the gap

  @integration
  Scenario: Shared questions are answered once
    Given a session has a pending batch of questions visible to two clients
    When one client submits the complete answers
    Then the session accepts the answers once
    And both clients observe that the questions are closed
