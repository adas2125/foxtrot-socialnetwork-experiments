{{- define "mongodb-sharded.connection" }}
  {{ .Values.global.mongodb.sharding.svc.user }}:{{ .Values.global.mongodb.sharding.svc.password }}@{{ .Values.global.mongodb.sharding.svc.name }}
{{- end }}

{{- define "memcached-cluster.connection" }}
  {{ .Release.Name }}-mcrouter
{{- end }}

{{- define "redis-cluster.connection" -}}
{{- if .Values.global.redis.cluster.external.enabled -}}
{{ required "global.redis.cluster.external.address is required when external Redis Cluster mode is enabled" .Values.global.redis.cluster.external.address }}
{{- else -}}
{{ .Release.Name }}-redis-cluster
{{- end -}}
{{- end }}

{{- define "socialnetwork.templates.other.service-config.json"  }}
{{- $externalRedisClusterEnabled := .Values.global.redis.cluster.external.enabled }}
{{- $externalRedisClusterRole := .Values.global.redis.cluster.external.role }}
{{- $socialGraphRedisClusterEnabled := or .Values.global.redis.cluster.enabled (and $externalRedisClusterEnabled (eq $externalRedisClusterRole "social-graph")) }}
{{- $homeTimelineRedisClusterEnabled := or .Values.global.redis.cluster.enabled (and $externalRedisClusterEnabled (eq $externalRedisClusterRole "home-timeline")) }}
{{- $composePostRedisClusterEnabled := .Values.global.redis.cluster.enabled }}
{{- $userTimelineRedisClusterEnabled := or .Values.global.redis.cluster.enabled (and $externalRedisClusterEnabled (eq $externalRedisClusterRole "user-timeline")) }}
{
    "secret": "secret",
    "social-graph-service": {
      "addr": "social-graph-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "social-graph-mongodb": {
      "addr": {{ ternary (include "mongodb-sharded.connection" . | trim) "social-graph-mongodb" .Values.global.mongodb.sharding.enabled | quote}},
      "port": {{ ternary .Values.global.mongodb.sharding.svc.port 27017 .Values.global.mongodb.sharding.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "social-graph-redis": {
      "addr": {{ ternary (include "redis-cluster.connection" . | trim) "social-graph-redis" $socialGraphRedisClusterEnabled | quote}},
      "port": 6379,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "use_cluster": {{ ternary 1 0 $socialGraphRedisClusterEnabled}},
      "use_replica": {{ ternary 1 0 .Values.global.redis.replication.enabled}}
    },
    "write-home-timeline-service": {
      "addr": "write-home-timeline-service",
      "port": 9090,
      "workers": 32,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "write-home-timeline-rabbitmq": {
      "addr": "write-home-timeline-rabbitmq",
      "port": 5672,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "home-timeline-redis": {
      "addr": {{ ternary (include "redis-cluster.connection" . | trim) "home-timeline-redis" $homeTimelineRedisClusterEnabled | quote}},
      "port": 6379,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "use_cluster": {{ ternary 1 0 $homeTimelineRedisClusterEnabled}},
      "use_replica": {{ ternary 1 0 .Values.global.redis.replication.enabled}}
    },
    "compose-post-service": {
      "addr": "compose-post-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "compose-post-redis": {
      "addr": {{ ternary (include "redis-cluster.connection" . | trim) "compose-post-redis" $composePostRedisClusterEnabled | quote}},
      "port": 6379,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "use_cluster": {{ ternary 1 0 $composePostRedisClusterEnabled}},
      "use_replica": {{ ternary 1 0 .Values.global.redis.replication.enabled}}
    },
    "user-timeline-service": {
      "addr": "user-timeline-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "user-timeline-mongodb": {
      "addr": {{ ternary (include "mongodb-sharded.connection" . | trim) "user-timeline-mongodb" .Values.global.mongodb.sharding.enabled | quote}},
      "port": {{ ternary .Values.global.mongodb.sharding.svc.port 27017 .Values.global.mongodb.sharding.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "user-timeline-redis": {
      "addr": {{ ternary (include "redis-cluster.connection" . | trim) "user-timeline-redis" $userTimelineRedisClusterEnabled | quote}},
      "port": 6379,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "use_cluster": {{ ternary 1 0 $userTimelineRedisClusterEnabled}},
      "use_replica": {{ ternary 1 0 .Values.global.redis.replication.enabled}}
    },
    "post-storage-service": {
      "addr": "post-storage-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "post-storage-mongodb": {
      "addr": {{ ternary (include "mongodb-sharded.connection" . | trim) "post-storage-mongodb" .Values.global.mongodb.sharding.enabled | quote}},
      "port": {{ ternary .Values.global.mongodb.sharding.svc.port 27017 .Values.global.mongodb.sharding.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "post-storage-memcached": {
      "addr": {{ ternary (include "memcached-cluster.connection" . | trim) "post-storage-memcached" .Values.global.memcached.cluster.enabled | quote}},
      "port": {{ ternary .Values.global.memcached.cluster.port 11211 .Values.global.memcached.cluster.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "binary_protocol": {{ ternary 0 1 .Values.global.memcached.cluster.enabled}}
    },
    "unique-id-service": {
      "addr": "unique-id-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "netif": "eth0"
    },
    "media-service": {
      "addr": "media-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "media-mongodb": {
      "addr": {{ ternary (include "mongodb-sharded.connection" . | trim) "media-mongodb" .Values.global.mongodb.sharding.enabled | quote}},
      "port": {{ ternary .Values.global.mongodb.sharding.svc.port 27017 .Values.global.mongodb.sharding.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "media-memcached": {
      "addr": {{ ternary (include "memcached-cluster.connection" . | trim) "media-memcached" .Values.global.memcached.cluster.enabled | quote}},
      "port": {{ ternary .Values.global.memcached.cluster.port 11211 .Values.global.memcached.cluster.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "binary_protocol": {{ ternary 0 1 .Values.global.memcached.cluster.enabled}}
    },
    "media-frontend": {
      "addr": "media-frontend",
      "port": 8081,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "text-service": {
      "addr": "text-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "user-mention-service": {
      "addr": "user-mention-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "url-shorten-service": {
      "addr": "url-shorten-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "url-shorten-memcached": {
      "addr": {{ ternary (include "memcached-cluster.connection" . | trim) "url-shorten-memcached" .Values.global.memcached.cluster.enabled | quote}},
      "port": {{ ternary .Values.global.memcached.cluster.port 11211 .Values.global.memcached.cluster.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "binary_protocol": {{ ternary 0 1 .Values.global.memcached.cluster.enabled}}
    },
    "url-shorten-mongodb": {
      "addr": {{ ternary (include "mongodb-sharded.connection" . | trim) "url-shorten-mongodb" .Values.global.mongodb.sharding.enabled | quote}},
      "port": {{ ternary .Values.global.mongodb.sharding.svc.port 27017 .Values.global.mongodb.sharding.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "user-service": {
      "addr": "user-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "netif": "eth0"
    },
    "user-memcached": {
      "addr": {{ ternary (include "memcached-cluster.connection" . | trim) "user-memcached" .Values.global.memcached.cluster.enabled | quote}},
      "port": {{ ternary .Values.global.memcached.cluster.port 11211 .Values.global.memcached.cluster.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000,
      "binary_protocol": {{ ternary 0 1 .Values.global.memcached.cluster.enabled}}
    },
    "user-mongodb": {
      "addr": {{ ternary (include "mongodb-sharded.connection" . | trim) "user-mongodb" .Values.global.mongodb.sharding.enabled | quote}},
      "port": {{ ternary .Values.global.mongodb.sharding.svc.port 27017 .Values.global.mongodb.sharding.enabled}},
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "home-timeline-service": {
      "addr": "home-timeline-service",
      "port": 9090,
      "connections": 512,
      "timeout_ms": 10000,
      "keepalive_ms": 10000
    },
    "ssl": {
      "enabled": false,
      "caPath": "/keys/CA.pem",
      "ciphers": "ALL:!ADH:!LOW:!EXP:!MD5:@STRENGTH",
      "serverKeyPath": "/keys/server.key",
      "serverCertPath": "/keys/server.crt"
    },
    "redis-primary": {
      "keepalive_ms": 10000,
      "addr": {{ .Values.global.redis.replication.primary | quote }},
      "timeout_ms": 10000,
      "port": 6379,
      "connections": 512
    },
    "redis-replica": {
      "keepalive_ms": 10000,
      "addr": {{ .Values.global.redis.replication.replica | quote }},
      "timeout_ms": 10000,
      "port": 6379,
      "connections": 512
    }
  }
  {{- end }}