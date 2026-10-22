from app.utils import dropbox_path, human_size, sanitize_filename


def test_sanitize_strips_directory_and_bad_chars():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename('a<b>c:d"e|f?.jpg') == "a_b_c_d_e_f_.jpg"
    assert sanitize_filename("  ..  ") == "telegram_file"


def test_sanitize_keeps_extension_and_limits_length():
    name = "x" * 300 + ".jpg"
    result = sanitize_filename(name)
    assert len(result) <= 200
    assert result.endswith(".jpg")


def test_sanitize_unicode_is_preserved():
    assert sanitize_filename("تقرير يومي.pdf") == "تقرير يومي.pdf"


def test_dropbox_path_normalises_slashes():
    assert dropbox_path("/folder/", "file.jpg") == "/folder/file.jpg"
    assert dropbox_path("folder", "file.jpg") == "/folder/file.jpg"
    assert dropbox_path("/", "file.jpg") == "/file.jpg"


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(8 * 1024 * 1024) == "8.0 MB"
