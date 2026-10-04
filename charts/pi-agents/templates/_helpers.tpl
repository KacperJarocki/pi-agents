{{- define "pi-agents.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "pi-agents.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- include "pi-agents.name" . }}
{{- end }}
{{- end }}

{{- define "pi-agents.labels" -}}
app.kubernetes.io/name: {{ include "pi-agents.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: iot-security
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
