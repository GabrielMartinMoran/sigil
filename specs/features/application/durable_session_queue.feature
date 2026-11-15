Feature: Durable server session queue
  The server accepts session messages durably before executing them.

  @integration
  Scenario: A message with images is committed before acknowledgement
    Given a server session and a client-generated request identifier
    When the client submits text and ordered images
    Then the server acknowledges only after the session and complete message are committed
    And the server's consumer can execute the message through the normal agent path

  @integration
  Scenario: Retrying a request does not duplicate its message
    Given a session has accepted a request identifier and payload
    When the same request identifier and payload are submitted again
    Then the existing acceptance is returned without a second execution
    And reuse of the identifier with a different payload is rejected

  @integration
  Scenario: An interrupted service does not replay accepted work
    Given a message was accepted before the service failed
    When the service opens the session again
    Then the message remains visible with an interrupted state
    And the message is not automatically executed again

  @integration
  Scenario: Stopping preserves undelivered accepted messages
    Given a session has an active execution and accepted messages not yet delivered
    When the session is stopped
    Then the undelivered messages remain visible as stopped
    And they do not execute without an explicit new user action

  @integration
  Scenario: A message submitted while busy retains steering semantics
    Given an agent turn is running in a session
    When a new message is accepted for that session
    Then the server delivers it as steering at an eligible seam
    And it does not silently turn it into an independent later turn

  @integration
  Scenario: A checkpoint atomically records steering delivery
    Given an accepted steering message is delivered during an active turn
    When the server checkpoints the transcript and queue progress
    Then the durable transcript and delivery record are committed together
    And the message appears once in the ordinary conversation view

  @integration
  Scenario: A recovered conflict preserves accepted messages and recovery access
    Given a session has an accepted message and its progress conflicts with another writer
    When the server saves progress in a recovery session
    Then the original session preserves the acceptance and identifies the recovery session
    And affected accepted messages appear separately from the transcript in the conflict notice
    And the divergent transcripts are not silently merged

  @integration
  Scenario: A later conflict does not hide an earlier acceptance
    Given an accepted message was checkpointed before a conflicting writer changed the session
    When the server preserves subsequent progress in a recovery session
    Then the original session still presents the affected acceptance in the conflict notice
    And the notice provides access to an available recovery session

  @integration
  Scenario: Cooperative server owners do not execute the same store concurrently
    Given a new server owns the session store
    When another new server attempts to own the same store
    Then the second server does not execute work against that store
    And it receives an explicit error
