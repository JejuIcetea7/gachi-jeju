#!/usr/bin/env bash
set -euo pipefail

printf 'VITE_KAKAO_MAP_KEY=%s\n' \
  "${VITE_KAKAO_MAP_KEY:?Set VITE_KAKAO_MAP_KEY in Elastic Beanstalk}" \
  > frontend/.env.production.local