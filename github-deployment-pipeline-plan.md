# GitHub Deployment Pipeline Plan
## Deploy `rag-app` to OCP on IBM Cloud via GitHub Actions

### Overview

Set up a full CI/CD pipeline using **GitHub Actions** that:

- Runs **tests on every pull request** — blocks merging on failure
- On **merge to `main`** — builds a Docker image, pushes to **GHCR** (`ghcr.io`), then deploys to **Red Hat OpenShift (OCP) on IBM Cloud** via the `oc` CLI
- Manages runtime secrets (API keys, tokens) by injecting them from **GitHub Actions Secrets** into an **OCP Kubernetes Secret** during the deploy job

The pipeline lives at `.github/workflows/` and is the only change to the existing codebase — the `rag-app` source code is untouched.

---

### Architecture

```
PR opened / push to branch
        ↓
[CI Workflow] pytest → pass/fail status check

Merge to main
        ↓
[CD Workflow]
  1. Build Docker image
  2. Log in to GHCR
  3. Push image → ghcr.io/ameeng/techxchange2-2026-automation:sha
  4. oc login → OCP cluster (IBM Cloud)
  5. Apply/update Kubernetes Secret (env vars)
  6. Apply Deployment + Service + Route manifests
  7. oc rollout status (wait for rollout to complete)
```

---

## Sub-Tasks

---

### Sub-Task 1 — Add a `Dockerfile` for the `rag-app`

**Status:** `[x] done`

**Intent**
The app needs a container image to run on OCP. This task produces a production-ready, minimal `Dockerfile` for the FastAPI/uvicorn app in `rag-app/`.

**Expected Outcomes**
- `rag-app/Dockerfile` exists and builds successfully with `docker build`
- Image starts the FastAPI server on port 8000
- `.dockerignore` excludes `.venv`, `__pycache__`, `.env`, `.git`

**Todo List**
1. Create `rag-app/Dockerfile` using a multi-stage build:
   - Stage 1 (`builder`): `python:3.12-slim` base, install `uv`, run `uv sync --no-dev` to install deps into `/app/.venv`
   - Stage 2 (`runtime`): copy only `/app` (with deps), set `CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]`
2. Create/update `rag-app/.dockerignore` to exclude: `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `.git/`, `*.log`, `.pytest_cache/`
3. Verify the image builds locally (documented note — not automated here)

**Relevant Context**
- [`rag-app/pyproject.toml`](rag-app/pyproject.toml) — lists all runtime deps (`fastapi`, `uvicorn`, `openai`, `python-dotenv`, `mcp`, `httpx`)
- [`rag-app/.python-version`](rag-app/.python-version) — Python 3.12 required
- [`rag-app/.gitignore`](rag-app/.gitignore) — existing ignore patterns to mirror in `.dockerignore`
- App loads `.env` at startup via `load_dotenv()` — environment variables must be injected at runtime (not baked into image)

---

### Sub-Task 2 — Add OCP Kubernetes Manifests

**Status:** `[x] done`

**Intent**
Define the OCP/Kubernetes objects needed to run the app: a `Deployment`, a `Service`, and an OCP `Route` (public HTTPS URL). These are declarative YAML files checked into the repo so the pipeline can `oc apply` them.

**Expected Outcomes**
- `k8s/deployment.yaml` — runs the container image, mounts env vars from a Secret, sets resource limits
- `k8s/service.yaml` — exposes port 8000 inside the cluster
- `k8s/route.yaml` — creates a public OCP Route (edge TLS termination)
- Manifests reference the image tag placeholder `IMAGE_TAG` (replaced by the pipeline at deploy time using `sed` or `envsubst`)

**Todo List**
1. Create `k8s/` directory at the repo root
2. Create `k8s/deployment.yaml`:
   - `image: ghcr.io/ameeng/techxchange2-2026-automation:IMAGE_TAG`
   - `envFrom` referencing a Secret named `rag-app-secrets`
   - `readinessProbe` on `GET /health`
   - `resources.limits` of `cpu: 500m, memory: 512Mi`
   - `replicas: 1`
3. Create `k8s/service.yaml`:
   - ClusterIP Service targeting port 8000
4. Create `k8s/route.yaml`:
   - OCP Route with `tls.termination: edge`
   - Points to the Service above
5. All manifests use namespace label `app: rag-app` for easy selection

**Relevant Context**
- [`rag-app/api.py`](rag-app/api.py) — `/health` endpoint available for readiness probe
- App expects `OPENCODE_ZEN_API_KEY`, `OPENCODE_ZEN_BASE_URL`, `OPENCODE_ZEN_MODEL`, `GITHUB_PERSONAL_ACCESS_TOKEN` as environment variables (see [`rag-app/.env.example`](rag-app/.env.example))
- Secret named `rag-app-secrets` will be created/updated by the deploy pipeline job (Sub-Task 4)

---

### Sub-Task 3 — CI Workflow: Test on Pull Requests

**Status:** `[x] done`

**Intent**
Create a GitHub Actions workflow that runs `pytest` on every pull request targeting `main`. This acts as a gate — the PR cannot be merged if tests fail.

**Expected Outcomes**
- `.github/workflows/ci.yml` exists
- Workflow triggers on `pull_request` targeting `main`
- Sets up Python 3.12 + `uv`, installs dev dependencies, runs `pytest`
- Status check appears on the PR

**Todo List**
1. Create `.github/workflows/ci.yml`
2. Trigger: `on: pull_request: branches: [main]`
3. Job steps:
   - `actions/checkout@v4`
   - Install `uv` via `astral-sh/setup-uv@v4`
   - `uv sync` (includes dev deps with `pytest`)
   - `uv run pytest rag-app/` with working directory set to `rag-app`
4. Set `runs-on: ubuntu-latest`

**Relevant Context**
- [`rag-app/pyproject.toml`](rag-app/pyproject.toml) — `pytest>=8.0` is in the `dev` dependency group
- No test files currently exist in the repo — the workflow will pass vacuously until tests are added (this is expected/acceptable)

---

### Sub-Task 4 — CD Workflow: Build, Push to GHCR, Deploy to OCP

**Status:** `[x] done`

**Intent**
Create the main deployment workflow that fires when code lands on `main`. It builds the Docker image, pushes it to GHCR with the commit SHA as the image tag, then logs into the OCP cluster and applies all manifests with the correct image tag.

**Expected Outcomes**
- `.github/workflows/cd.yml` exists
- Workflow triggers on `push` to `main`
- Docker image pushed to `ghcr.io/ameeng/techxchange2-2026-automation:<sha>`
- OCP Secret `rag-app-secrets` created/updated with current secret values from GitHub Secrets
- All three manifests applied (`deployment`, `service`, `route`)
- `oc rollout status` confirms successful rollout before the job completes

**Todo List**
1. Create `.github/workflows/cd.yml`
2. Trigger: `on: push: branches: [main]`
3. Permissions block: `packages: write, contents: read`
4. **Build & Push** job steps:
   - `actions/checkout@v4`
   - `docker/metadata-action@v5` — generates tags (`sha-<short>`, `latest`)
   - Log in to GHCR: `echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin`
   - `docker/build-push-action@v6` — build from `rag-app/Dockerfile`, push to `ghcr.io/ameeng/techxchange2-2026-automation`
5. **Deploy** job (depends on Build & Push):
   - `redhat-actions/oc-login@v1` — authenticates using `OPENSHIFT_SERVER_URL` and `OPENSHIFT_TOKEN` secrets
   - `oc project ${{ vars.OPENSHIFT_NAMESPACE }}`
   - Create/replace OCP Secret `rag-app-secrets` using `oc create secret generic ... --from-literal` for each env var (using `--save-config --dry-run=client -o yaml | oc apply -f -` pattern to be idempotent)
   - `envsubst` or `sed` to replace `IMAGE_TAG` placeholder in `k8s/deployment.yaml` with the actual SHA
   - `oc apply -f k8s/`
   - `oc rollout status deployment/rag-app --timeout=120s`

**Relevant Context**
- Required GitHub Actions Secrets to configure:
  - `OPENSHIFT_SERVER_URL` — OCP cluster API URL (`oc whoami --show-server`)
  - `OPENSHIFT_TOKEN` — Service account token (see Sub-Task 5)
  - `OPENCODE_ZEN_API_KEY` — from `.env.example`
  - `OPENCODE_ZEN_BASE_URL` — from `.env.example`
  - `OPENCODE_ZEN_MODEL` — from `.env.example`
  - `GITHUB_PERSONAL_ACCESS_TOKEN` — GitHub PAT for the MCP server
- `GITHUB_TOKEN` is auto-provided by Actions — used for GHCR login (no extra secret needed)
- `OPENSHIFT_NAMESPACE` stored as a GitHub Actions **Variable** (not secret) — e.g. `rag-app-ns`
- `redhat-actions/oc-login@v1` — official Red Hat action for OCP auth

---

### Sub-Task 5 — OCP Service Account Setup (One-Time Manual Step)

**Status:** `[x] done`

**Intent**
The pipeline needs a stable, long-lived token to authenticate with OCP. A dedicated OCP Service Account (SA) with `edit` role in the target namespace provides this — it avoids using a personal token that expires.

**Expected Outcomes**
- OCP Service Account `github-actions-deployer` exists in the target namespace
- SA has `edit` role in the namespace (can apply Deployments, Services, Routes, Secrets)
- A long-lived token Secret is extracted and stored as `OPENSHIFT_TOKEN` in GitHub Actions Secrets

**Todo List**
1. Log into OCP cluster locally: `oc login <cluster-url>`
2. Create or select the target namespace: `oc new-project rag-app-ns` (or `oc project rag-app-ns`)
3. Create the Service Account: `oc create serviceaccount github-actions-deployer -n rag-app-ns`
4. Grant edit role: `oc policy add-role-to-user edit -z github-actions-deployer -n rag-app-ns`
5. Create a long-lived token secret (required for OCP 4.11+):
   ```
   oc create secret generic github-actions-sa-token \
     --type=kubernetes.io/service-account-token \
     --annotation="kubernetes.io/service-account.name=github-actions-deployer" \
     -n rag-app-ns
   ```
6. Retrieve the token: `oc get secret github-actions-sa-token -n rag-app-ns -o jsonpath='{.data.token}' | base64 -d`
7. Add the token value as `OPENSHIFT_TOKEN` in GitHub → Settings → Secrets → Actions
8. Add the cluster API URL as `OPENSHIFT_SERVER_URL`: `oc whoami --show-server`

**Relevant Context**
- OCP 4.11+ no longer auto-creates long-lived SA tokens — explicit Secret of type `kubernetes.io/service-account-token` is required (confirmed by Red Hat docs)
- This is a **one-time manual setup** — not automated by the pipeline itself
- `OPENSHIFT_NAMESPACE` should be set as a GitHub Actions **Variable** (non-secret), not a Secret

---

### Sub-Task 6 — Configure GitHub Branch Protection

**Status:** `[x] done`

**Intent**
Enforce that the CI check passes before any PR can be merged to `main`, giving the test gate real teeth.

**Expected Outcomes**
- `main` branch is protected in GitHub repository settings
- The `pytest` CI status check is required before merging
- Direct pushes to `main` are blocked (PRs required)

**Todo List**
1. Go to GitHub → `ameeng/techxchange2-2026-automation` → Settings → Branches
2. Add branch protection rule for `main`:
   - ✅ Require status checks to pass before merging
   - Add `pytest` (from the CI workflow job name) as a required status check
   - ✅ Require branches to be up to date before merging
   - ✅ Restrict direct pushes (require PR)
3. Save the protection rule

**Relevant Context**
- The CI workflow job name becomes the status check name that appears in GitHub
- Branch protection cannot be configured via files — it is a manual GitHub UI step
- This sub-task has no code changes

---

### GitHub Secrets & Variables Summary

| Name | Type | Where set | Value source |
|---|---|---|---|
| `OPENSHIFT_SERVER_URL` | Secret | GitHub Actions Secrets | `oc whoami --show-server` |
| `OPENSHIFT_TOKEN` | Secret | GitHub Actions Secrets | SA token from Sub-Task 5 |
| `OPENCODE_ZEN_API_KEY` | Secret | GitHub Actions Secrets | Your OpenCode Zen key |
| `OPENCODE_ZEN_BASE_URL` | Secret | GitHub Actions Secrets | `https://opencode.ai/zen/v1` |
| `OPENCODE_ZEN_MODEL` | Secret | GitHub Actions Secrets | `deepseek-v4-flash` |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | Secret | GitHub Actions Secrets | GitHub PAT (repo, read:org, read:user) |
| `OPENSHIFT_NAMESPACE` | Variable | GitHub Actions Variables | `rag-app-ns` (or your namespace) |
| `GITHUB_TOKEN` | Auto | Provided by Actions | No action needed |

---

### Files to Create

```
techxchange2-2026-automation/
├── .github/
│   └── workflows/
│       ├── ci.yml               ← Sub-Task 3
│       └── cd.yml               ← Sub-Task 4
├── k8s/
│   ├── deployment.yaml          ← Sub-Task 2
│   ├── service.yaml             ← Sub-Task 2
│   └── route.yaml               ← Sub-Task 2
└── rag-app/
    └── Dockerfile               ← Sub-Task 1
```
