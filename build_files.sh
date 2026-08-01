#!/bin/bash
set -e

python3 -m pip install --disable-pip-version-check -r requirements.txt
python3 manage.py migrate --noinput
python3 manage.py collectstatic --noinput
