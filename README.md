# github-clone

Pojednostavljeni backend GitHuba napravljen kao mikroservisna aplikacija. Projekt je rađen za kolegij Raspodijeljeni sustavi (FIPU).

Korisnik se može registrirati i prijaviti, kreirati repozitorije (javne ili privatne), inicijalizirati git repozitorij na disku te pregledavati grane, commitove i datoteke. Sve se pokreće lokalno kroz Docker Compose.

## Arhitektura

![image info](./Docs/Arhitecture.png)


| Servis | Zadatak | Tehnologija |
|---|---|---|
| `gateway` | jedina ulazna točka, rutiranje, rate limiting | nginx |
| `auth-service` | registracija, prijava, izdavanje JWT tokena | FastAPI, bcrypt, python-jose |
| `repo-service` | CRUD nad metapodacima repozitorija, provjera prava pristupa | FastAPI, psycopg2 |
| `git-service` | rad s bare git repozitorijima na disku | FastAPI (async), GitPython, httpx |
| `db-primary` | PostgreSQL baza | PostgreSQL 15 |
| `db-replica` | streaming replika primarne baze | PostgreSQL 15 |

Servisi međusobno ne dijele stanje osim baze. JWT token potpisuje `auth-service`, a ostali servisi ga provjeravaju sami pomoću zajedničkog `SECRET_KEY`, bez poziva prema `auth-serviceu`.

`git-service` nema pristup bazi. Prije svake operacije pita `repo-service` smije li korisnik čitati ili pisati u repozitorij (`/internal/repositories/{id}/access`). Taj endpoint nije izložen kroz gateway.

## Pokretanje

Potrebno: Docker i Docker Compose.

```bash
cp .env.example .env
```

U `.env` upisati vrijednosti:

| Varijabla | Opis |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | pristup bazi |
| `REPLICATION_PASSWORD` | lozinka korisnika `replicator` za repliku |
| `SECRET_KEY` | ključ za potpis JWT tokena |
| `TIMING` | `1` uključuje ispis mjerenja vremena u git-serviceu, zadano `0` |

```bash
docker compose up -d --build
```

API je dostupan na `http://localhost:8080`. Stanje servisa:

```bash
docker compose ps
curl http://localhost:8080/health
```

Napomene:

- nakon promjene koda servis treba ponovno izgraditi: `docker compose up -d --build <servis>`
- `db/init.sql` se izvršava samo kad je volumen baze prazan, `docker compose down -v` briše sve podatke
- `db/replication.sh` mora imati LF završetke redova (riješeno kroz `.gitattributes`)

## Pregled baze

Baza nije izložena prema van, pristupa joj se kroz kontejner:

```bash
docker compose exec db-primary psql -U <POSTGRES_USER> -d <POSTGRES_DB>
```

Korisne naredbe unutar `psql`: `\dt` za popis tablica, `\d users` za strukturu tablice, `\x` za okomiti ispis, `\q` za izlaz. Rola `postgres` u ovoj bazi ne postoji, pa korisnik i baza moraju biti navedeni izričito.

Jednokratni upit bez ulaska u `psql`:

```bash
docker compose exec db-primary sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT id, username, email, created_at FROM repositories;"'
```

```bash
docker compose exec db-primary sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT id, name, owner_id, is_private, created_at FROM repositories;"'
```
Isti upit nad `db-replica` pokazuje da replikacija radi. Replika je u stanju oporavka i prima samo čitanje.

## API

Svi endpointi osim registracije i prijave traže zaglavlje `Authorization: Bearer <token>`.

### Auth

| Metoda | Putanja | Opis |
|---|---|---|
| POST | `/api/auth/register` | registracija (`username`, `email`, `password`) |
| POST | `/api/auth/login` | prijava, vraća `access_token` (vrijedi 60 min) |

### Repozitoriji

| Metoda | Putanja | Opis |
|---|---|---|
| POST | `/api/repositories` | novi repozitorij (`name`, `description`, `is_private`) |
| GET | `/api/repositories` | repozitoriji prijavljenog korisnika |
| GET | `/api/repositories/{id}` | jedan repozitorij |
| PATCH | `/api/repositories/{id}` | izmjena `description` / `is_private` |
| DELETE | `/api/repositories/{id}` | brisanje |

Privatni repozitorij tuđem korisniku vraća `404`, a ne `403`, da se ne otkrije da postoji.

### Git

| Metoda | Putanja | Opis |
|---|---|---|
| POST | `/api/repos/init` | inicijalizira bare git repozitorij (`repo_id`) |
| GET | `/api/repos/{id}/branches` | grane |
| GET | `/api/repos/{id}/commits?branch=master&limit=20` | commitovi na grani |
| GET | `/api/repos/{id}/tree?branch=master&path_prefix=` | sadržaj direktorija |

### Clone i pull

Bare repozitoriji se poslužuju statički na `/git/{id}`, pa se mogu klonirati i povlačiti običnim gitom:

```bash
git clone http://localhost:8080/git/<repo_id> proba
```

To je gitov „dumb” HTTP protokol: nginx samo poslužuje datoteke iz repozitorija, a git klijent sam slaže objekte. Datoteke `info/refs` i `objects/info/packs`, koje dumb protokol traži, održava hook `post-update` koji se postavlja pri `/api/repos/init`.

### Push

Push ne ide preko HTTP-a. Bare repozitoriji stoje u folderu `repos-data/`, pa se u njih piše izravno, s lokalnom putanjom kao remoteom:

```bash
git remote add origin ./repos-data/<repo_id>
git push origin master
```

To je gitov lokalni transport, a ne mrežni protokol, pa radi samo s računala na kojem servis stoji. Grana mora biti `master`, jer je to zadana vrijednost parametra `branch` na git rutama.

## Otpornost i skaliranje

**Rate limiting.** Gateway ograničava prijavu i registraciju na 5 zahtjeva u minuti po IP adresi (burst 3), a ostale rute na 20 zahtjeva u sekundi (burst 10). Prekoračenje vraća `429`.

**Retry.** Poziv `git-service → repo-service` ponavlja se do 3 puta s eksponencijalnim čekanjem (100 ms, 200 ms), i to samo kod mrežne greške ili `503`. Poziv je čitanje, pa ponavljanje nema nuspojava.

**Replikacija baze.** `db-replica` se pri prvom pokretanju klonira s primarne baze (`pg_basebackup`) i dalje prima promjene kroz WAL streaming. Stanje replikacije:

```bash
docker compose exec db-primary psql -U <POSTGRES_USER> -d <POSTGRES_DB> \
  -c "SELECT client_addr, state FROM pg_stat_replication;"
```

**Health checkovi.** Svaki servis ima `/health` endpoint koji Docker periodički provjerava. Nginx neovisno o tome prati stvarne zahtjeve: ako instanca dvaput ne odgovori unutar 10 sekundi, 10 sekundi joj ne šalje promet (`max_fails=2`, `fail_timeout=10s`).

**Više instanci.** Servisi su bez stanja pa se mogu pokrenuti u više kopija, nginx tada raspoređuje zahtjeve između njih:

```bash
docker compose up -d --scale repo-service=3
docker compose restart gateway
```

`git-service` se može skalirati jer sve instance dijele isti volumen `/repos`.

## Async i mjerenja

`git-service` je napisan asinkrono: poziv prema `repo-serviceu` ide kroz jedan dijeljeni `httpx.AsyncClient`, a blokirajuće GitPython operacije izvršavaju se u threadpoolu kroz `asyncio.to_thread`. `auth-service` i `repo-service` su sinkroni, FastAPI ih izvršava u svom threadpoolu.

Mjereno s `ab -n 2000` na `/api/repos/{id}/branches` (poziv prolazi kroz git-service i repo-service), s privremeno isključenim rate limitom:

| Konkurentnost | sync git-service | async git-service |
|---|---|---|
| 1 | 95 req/s | 158 req/s |
| 160 | 279 req/s | 288 req/s |

Razlika pri konkurentnosti 1 ne dolazi od asinkronosti, nego od toga što sinkrona verzija za svaki poziv otvara novu TCP konekciju, a `AsyncClient` ih ponovno koristi (keep-alive). Pri većoj konkurentnosti razlika gotovo nestaje jer usko grlo postaje `repo-service`. Detaljna analiza je u zasebnom dokumentu.

Za ispis vremena pojedinih koraka postaviti `TIMING=1` u `.env` i ponovno pokrenuti `git-service`.

## Ograničenja

- Nema `git push` preko HTTP-a. Sadržaj se u repozitorij ne može poslati kroz API; kloniranje i povlačenje rade kroz dumb HTTP.
- Ruta `/git/` nema autentikaciju. Poslužuje sadržaj svih repozitorija, uključujući privatne, pa ih može klonirati svatko tko zna `id`. Provjera prava pristupa vrijedi samo za `/api/` rute. Statičko posluživanje ne može provjeriti JWT, a git klijent ga ionako ne šalje.
- Replika se ne koristi za čitanje i nema automatskog failovera. Ako primarna baza padne, servisi ne prelaze sami na repliku.
- Neuspješan health check ne pokreće ponovno kontejner. `restart: unless-stopped` reagira samo na pad procesa, a Docker Compose nema orkestrator koji bi gledao health status. Za to bi trebao vanjski nadzorni servis s pristupom docker socketu, koji ovdje nije napravljen.
- Gateway pri pokretanju razrješava imena svih servisa iz `upstream` blokova. Ako neki servis nije pokrenut, nginx se odbija pokrenuti uz `host not found in upstream`, pa pad jednog servisa sprječava podizanje ulazne točke. Dok već radi, nedostupan servis tretira kroz `max_fails` i ne ruši se.
- `repo-service` i `auth-service` otvaraju novu konekciju prema bazi za svaki zahtjev.
- Brisanje repozitorija u `repo-serviceu` ne briše git repozitorij s diska.