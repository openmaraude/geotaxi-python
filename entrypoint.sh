#!/bin/bash

cmdline=""

# Arguments with values to provide if set
for value_env in \
    HOST \
    PORT \
    WORKERS \
    REDIS_HOST \
    REDIS_PORT \
    REDIS_PASSWORD \
    FLUENT_HOST \
    FLUENT_PORT \
    API_URL;
do
    value=$(eval "echo \${$value_env}")
    test "$value" == "" && continue

    argname=$(echo $value_env | awk '{print tolower($0)}' | sed 's/_/-/g')
    cmdline="$cmdline --$argname $value"
done

# Boolean values to provide only if not empty
for bool_env in \
    DISABLE_FLUENT \
    VERBOSE \
    AUTH_ENABLED;
do
    value=$(eval "echo \${$bool_env}")
    test "$value" == "" && continue
    argname=$(echo $bool_env | awk '{print tolower($0)}' | sed 's/_/-/g')
    cmdline="$cmdline --$argname"
done


exec geotaxi $cmdline
