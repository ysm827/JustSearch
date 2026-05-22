# JustSearch

<p align="center">
  <a href="./README.md">中文</a> | <a href="./README.en.md">English</a>
</p>

An autonomous AI deep-search agent powered by Playwright, iterative task planning, deep web crawling, and cited multi-source synthesis.

## Overview

An autonomous AI deep-search agent powered by Playwright, iterative task planning, deep web crawling, and cited multi-source synthesis.

## Features

- Breaks search goals into iterative investigation steps.
- Uses Playwright-driven browsing for deeper web access.
- Synthesizes multi-source findings with citation traces.
- Provides Docker, helper scripts, and manual developer workflows.

## Quick Start

- Run `./run.sh` or `run.bat` to start the project.
- For development, enter `backend` and install the Python dependencies from `backend/requirements.txt`.
- Docker users can use the included `Dockerfile` and `docker-compose.yml`.

## Configuration

- Configure model, search, and browser runtime settings through backend environment variables.
- When captcha or anti-bot checks appear, follow the captcha guidance in the Chinese README.

## Tech Stack

- Python
- Playwright
- Docker

## Project Structure

- `backend`
- `tools`
- `tests`

## Contributing

Issues and pull requests are welcome. Before submitting changes, review the existing structure and keep contributions focused and verifiable.

## License

License information is available in the repository `LICENSE` file.
