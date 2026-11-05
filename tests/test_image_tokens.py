"""The `[Image #n]` marker as a unit the user can delete, renumber and send.

The failure this module prevents is quiet: a screenshot half-deleted leaves
`[Imag` in the message, the send still carries the picture, and the model reads a
caption that points at nothing. So the marker behaves like the block it stands
for — one key removes all of it — and the numbers are kept contiguous after every
edit, because `[Image #2]` without an `[Image #1]` is a dangling reference.
"""

from __future__ import annotations

from src.engine.attachments.tokens import (
    Deletion,
    block_deletion,
    image_at,
    location,
    numbers_in,
    offset,
    renumber,
    spans,
    token,
)

# --- the marker in the text -------------------------------------------------


def test_spans_find_every_marker_with_its_number():
    found = spans('antes [Image #1] en medio [Image #2] al final')

    assert [span.number for span in found] == [1, 2]
    assert found[0].text == '[Image #1]'


def test_a_marker_lookalike_typed_by_hand_is_not_a_marker():
    """`[image 1]` in prose is prose; only the shape the composer writes is attached."""
    assert spans('see [image 1] and [Image1] and [Image #]') == []


def test_location_and_offset_are_inverses():
    text = 'primera línea\nsegunda [Image #1]\ntercera'

    assert location(text, offset(text, (1, 8))) == (1, 8)
    assert location(text, 0) == (0, 0)


def test_image_at_finds_the_marker_under_the_cursor():
    text = 'mirá [Image #1] esto'
    found = image_at(text, (0, 8))

    assert found is not None and found.number == 1


def test_a_cursor_right_after_the_marker_is_outside_it():
    """That position is where a backspace acts from; it is not "inside"."""
    text = 'mirá [Image #1] esto'

    assert image_at(text, (0, 15)) is None


# --- deleting it as a block -------------------------------------------------


def test_backspace_right_after_a_marker_removes_the_whole_marker():
    text = 'mirá [Image #1] esto'
    cursor = (0, 15)

    deletion = block_deletion(text, (cursor, cursor), backspace=True)

    assert deletion == Deletion(5, 15)


def test_delete_right_before_a_marker_removes_the_whole_marker():
    text = 'mirá [Image #1] esto'

    deletion = block_deletion(text, ((0, 5), (0, 5)), backspace=False)

    assert deletion == Deletion(5, 15)


def test_backspace_in_the_middle_of_prose_is_left_to_the_text_area():
    """The key has to keep working as a key: one character, not a block."""
    text = 'una frase normal'
    cursor = (0, 8)

    assert block_deletion(text, (cursor, cursor), backspace=True) is None


def test_a_selection_that_cuts_through_a_marker_is_widened_to_all_of_it():
    """Selecting half a screenshot and deleting it cannot leave `[Ima` behind."""
    text = 'mirá [Image #1] esto'

    deletion = block_deletion(text, ((0, 7), (0, 18)), backspace=True)

    # It starts at the marker's first character (5) and keeps the end the user
    # chose (18): the selection took more than the marker, so only the beginning
    # needs widening.
    assert deletion == Deletion(5, 18)


def test_a_selection_that_does_not_touch_a_marker_is_left_alone():
    text = 'mirá [Image #1] esto'

    assert block_deletion(text, ((0, 0), (0, 4)), backspace=True) is None


def test_a_marker_at_the_start_of_the_line_is_deleted_by_delete():
    text = '[Image #1] con texto'

    assert block_deletion(text, ((0, 0), (0, 0)), backspace=False) == Deletion(0, 10)


# --- numbering --------------------------------------------------------------


def test_renumber_keeps_the_numbers_contiguous_after_a_deletion():
    """`[Image #2]` alone would point at a picture that is no longer attached."""
    assert renumber('[Image #2] y [Image #3]', [2, 3]) == '[Image #1] y [Image #2]'


def test_renumber_drops_the_marker_of_an_image_that_is_gone():
    assert renumber('ver [Image #1] esto', [2]) == 'ver esto'


def test_renumber_drops_a_leading_marker_without_leaving_a_space():
    assert renumber('[Image #1] con texto', []) == 'con texto'


def test_renumber_leaves_a_message_without_markers_untouched():
    assert renumber('sin imágenes', []) == 'sin imágenes'


def test_numbers_in_reports_the_order_and_ignores_repeats():
    """Pasting the same picture twice is one attachment with two mentions."""
    assert numbers_in('[Image #1] texto [Image #1] y [Image #2]') == [1, 2]


def test_token_and_spans_agree_on_the_shape():
    assert spans(token(7))[0].number == 7
