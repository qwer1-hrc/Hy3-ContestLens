#!/usr/bin/env sh
set -eu
BASE_URL="${HY3_CONTESTLENS_URL:-http://127.0.0.1:8000}"
curl --fail --silent --show-error "$BASE_URL/healthz"
curl --fail --silent --show-error "$BASE_URL/api/v1/system/capabilities"
curl --fail --silent --show-error "$BASE_URL/api/v1/datasets/noip2018/problems"

