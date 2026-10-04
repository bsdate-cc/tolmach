from tolmach.tray import icons

HEAD = (32, 20)        # inside the microphone's head
BESIDE = (10, 32)      # a plain disc would cover this point; a microphone does not
TRANSPARENT = 0


def test_every_state_is_a_64px_rgba_image():
    for state in (icons.IDLE, icons.RECORDING, icons.FINISHING, icons.DOWN, icons.ERROR):
        image = icons.image(state)
        assert image.size == (64, 64) and image.mode == "RGBA"


def test_the_head_colour_tells_idle_recording_and_finishing_apart():
    assert icons.image(icons.IDLE).getpixel(HEAD) == icons.GREY
    assert icons.image(icons.RECORDING).getpixel(HEAD) == icons.RED
    assert icons.image(icons.FINISHING).getpixel(HEAD) == icons.YELLOW


def test_the_icon_is_a_microphone_not_a_disc():
    for state in (icons.IDLE, icons.RECORDING, icons.FINISHING, icons.DOWN):
        assert icons.image(state).getpixel(BESIDE)[3] == TRANSPARENT


def test_down_is_the_outline_of_the_microphone():
    image = icons.image(icons.DOWN)
    assert image.getpixel(HEAD)[3] == TRANSPARENT
    assert image.getpixel((23, 20)) == icons.GREY


def test_error_is_a_red_cross_over_the_microphone():
    image = icons.image(icons.ERROR)
    assert image.getpixel((32, 32)) == icons.RED
    assert image.getpixel(HEAD) == icons.GREY


def test_unknown_state_looks_idle():
    assert icons.image("whatever").getpixel(HEAD) == icons.GREY
