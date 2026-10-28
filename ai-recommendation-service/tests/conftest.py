"""Loaded by pytest before any test module is collected.

app.database builds its engine from settings at import time, so DATABASE_URL has to point at the
test database before the first test module imports the app. Setting it inside one test module is
too late whenever another module that imports the app is collected first.
"""

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
