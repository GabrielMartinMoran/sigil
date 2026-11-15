Feature: Session event hub foundation
  Internal event ownership isolates filtered delivery without changing wire data.
  This foundation does not yet migrate runtime publishers or client subscriptions.

  @unit
  Scenario: Internal ownership preserves event serialization
    Given representative events including a turn payload with a session_id field
    When internal session ownership is assigned
    Then serialized SSE bytes and dictionary keys remain unchanged
    And the original payload is preserved

  @unit
  Scenario: Live delivery isolates sessions
    Given subscriptions for sessions alpha and beta and an internal unfiltered observer
    When alpha, beta and service-wide events are published in order
    Then each filtered subscription receives its own and service-wide events only
    And the unfiltered observer receives all events in order

  @unit
  Scenario: Replay uses the same ownership filter
    Given interleaved events for alpha and beta and service-wide events
    When alpha requests replay after a global cursor
    Then replay returns matching events after the cursor in original order
    And gaps in global sequence numbers do not add foreign events

  @unit
  Scenario: Foreign traffic cannot overflow a filtered queue
    Given a bounded subscription for alpha
    When many beta events are published
    Then alpha queue space is not consumed
    And alpha does not receive a lag marker

  @unit
  Scenario: Overflow affects only its subscriber
    Given filtered subscriptions with independent bounded queues
    When one queue overflows with events matching its filter
    Then only the affected subscriber receives the lag marker
    And the marker carries that subscriber's internal session scope
    And existing lag marker wire behavior is preserved

  @unit
  Scenario: Closing a subscription releases its queue
    Given an active filtered subscription
    When it is closed
    Then later publications no longer enqueue into it
    And other subscriptions continue receiving matching events
