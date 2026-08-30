# OCR preprocessing fixtures

The parity suite generates non-sensitive text and pixel fixtures at test time.
It records the selected system TrueType font in the subtest label and compares
the Python and native backends within the same process. No captured user screen
content or OCR result is stored here.
