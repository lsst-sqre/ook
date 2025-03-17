#!/bin/bash

set -eu

ook init
uvicorn ook.main:app --host 0.0.0.0 --port 8080
