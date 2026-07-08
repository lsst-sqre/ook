#!/bin/bash

set -eu

uvicorn ook.main:app --host 0.0.0.0 --port 8080
