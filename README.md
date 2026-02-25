# Vision System (Mac + k3d)

Minimal end-to-end industrial CV system (task-agnostic):

- Edge client sends image
- FastAPI Gateway validates + routes task
- Triton serves model
- Raw image stored in MinIO (S3)
- Metadata + predictions stored in Postgres
- (Later) Evidently drift + LGTM observability + training loop

## Quick start (local)

1) Create k3d cluster
2) Deploy data services (NATS/MinIO/Postgres)
3) Deploy Triton + Gateway
4) Run edge test client

See `scripts/` for commands.