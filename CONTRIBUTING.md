# Contributing

Thanks for helping out. Bug reports, fixes and improvements are welcome as issues or pull requests on [GitHub](https://github.com/FreddyyAndrews/poker-tournament-runner).

## What you can contribute

- Bug reports: what you ran, what you expected and what happened. A run log from `data/runs/` or a seed that reproduces it helps a lot.
- Fixes and improvements to the `poker` CLI, the server, the web UI and the docs
- New reference bots in `bots/`

## Before you open a PR

```bash
make test        # engine, runner and arena tests
make web-test    # if you changed anything under web/
```

If you changed `sandbox/runner.py`, rebuild the image with `./sandbox.sh build` and check that a `--docker` match still runs.

## What stays stable

Participants write bots against these, so they don't change without a good reason, and never close to an event:

- `engine/game.py`: the poker rules
- `sandbox/runner.py`: the bot protocol, i.e. how `decide()` is called and the `game_state` it receives
- `sandbox/Dockerfile`: the libraries available to bots

New `game_state` keys can be added, but existing keys keep their names and meaning.

## Adding a reference bot

Copy `bots/template/` into a new folder under `bots/`, implement `decide()`, check it with `make validate BOT=bots/<name>/bot.py`, add a row to the reference bots table in the README and open a PR. Bots that show a distinct strategy are the most useful.

## Requesting a library for the sandbox

The sandbox ships with `eval7`, `numpy`, `scipy`, `treys` and `scikit-learn` plus the Python 3.10 standard library. If you need something else, open an issue titled `[library-request] <package>` well before the event, since the image is rebuilt once and then frozen.

Generally accepted:
- Numerical and scientific libraries (e.g. `pandas`, `networkx`)
- Algorithm and data-structure libraries with no network or filesystem behaviour

Generally rejected:
- Anything that needs network access (bots run with `--network none` anyway)
- Heavy ML training frameworks (`pytorch`, `tensorflow`, `jax`). Train offline and ship weights as `.npz` in `data/`
- Native binaries that can't be cleanly `pip install`-ed

## License

By contributing, you agree that your contributions are licensed under the project's [MIT License](LICENSE).
