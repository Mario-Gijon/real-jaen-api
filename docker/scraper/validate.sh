#!/bin/sh
# Usage: sh validate.sh IMAGE NEW_EVIDENCE_DIRECTORY
# This starts only disposable offline probes, never production services or downloads.
set -eu
image=${1:?Image required}
out=${2:?New evidence directory required}
test ! -e "$out"
test "$(docker version --format '{{.Server.Os}}')" = linux
mkdir -p "$out"
name="rj-scraper-offline-$(date -u +%Y%m%d%H%M%S)-$$"
volume="$name-profile"
existing=$(docker volume ls --format '{{.Name}}')
if printf '%s\n' "$existing" | grep -Fxq "$volume"; then
    echo 'Refusing to reuse an existing volume' >&2
    exit 1
fi
docker volume create --label "rj.offline=$name" "$volume" > "$out/volume-name.txt"
test "$(docker volume inspect "$volume" --format '{{index .Labels "rj.offline"}}')" = "$name"
docker volume inspect "$volume" > "$out/volume.json"
containers=''
cleanup() {
    for container in $containers; do
        if identity=$(docker inspect "$container" --format '{{index .Config.Labels "rj.offline"}}' 2>/dev/null) && [ "$identity" = "$name" ]; then
            docker rm -f "$container" >/dev/null 2>&1 || true
        fi
    done
    echo "Evidence: $out; exclusive fixture volume retained: $volume"
}
trap cleanup EXIT
docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN \
    --mount "type=volume,source=$volume,target=/acquisition" --entrypoint chown \
    "$image" 10001:10001 /acquisition

probe() {
    phase=$1
    shift
    container="$name-$phase"
    containers="$containers $container"
    docker create --name "$container" --label "rj.offline=$name" --init --network none \
        --cap-drop ALL --security-opt no-new-privileges:true --shm-size 512m \
        --mount "type=volume,source=$volume,target=/acquisition" \
        "$image" "$@" --report "$phase.json" > "$out/$phase-container.txt"
    docker inspect "$container" > "$out/$phase-inspect.json"
    if docker start -a "$container" > "$out/$phase.log" 2>&1; then
        code=0
    else
        code=$?
    fi
    docker cp "$container:/acquisition/$phase.json" "$out/$phase.json"
    cat "$out/$phase.json"
    test "$code" -eq 0
}
probe dependencies --phase dependencies
probe storage-repeat --phase dependencies --expect-storage
probe browser --phase browser --expect-storage
probe browser-repeat --phase browser --expect-storage --expect-cookie

# Only reached if the protected browser started and profile cookies survived recreation.
container="$name-termination"
containers="$containers $container"
docker create --name "$container" --label "rj.offline=$name" --init --network none \
    --cap-drop ALL --security-opt no-new-privileges:true --shm-size 512m \
    --mount "type=volume,source=$volume,target=/acquisition" \
    "$image" --phase browser --expect-storage --expect-cookie --hold --report termination.json

docker start "$container"
ready=false
for attempt in $(seq 1 90); do
    if docker exec "$container" test -f /acquisition/ready.json; then ready=true; break; fi
    test "$(docker inspect "$container" --format '{{.State.Running}}')" = true
    sleep 1
done
test "$ready" = true
docker stop --time 15 "$container"
test "$(docker inspect "$container" --format '{{.State.ExitCode}}')" = 0
docker logs "$container" > "$out/termination.log" 2>&1
docker cp "$container:/acquisition/termination.json" "$out/termination.json"
cat "$out/termination.json"
echo 'OFFLINE_PACKAGING_OK: dependencies,non-root,persistent-profile,browser,child-cleanup,SIGTERM'
