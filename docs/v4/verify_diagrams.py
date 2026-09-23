"""Render the six authoritative Mermaid diagrams using an explicit local installation."""

import argparse
from functools import partial
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Thread

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-modules", type=Path, required=True)
    parser.add_argument("--browser", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).with_name("IMPLEMENTATION-BLUEPRINT.md").read_text()
    diagrams = re.findall(r"```mermaid\n(.*?)\n```", source, re.S)
    assert len(diagrams) == 6, f"expected six diagrams, found {len(diagrams)}"
    package = json.loads((args.node_modules / "mermaid/package.json").read_text())
    assert package["version"] == "12.0.0", "use reviewed Mermaid version"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(args.node_modules)))
    Thread(target=server.serve_forever, daemon=True).start()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            for index, diagram in enumerate(diagrams, 1):
                rendered = page.evaluate("""async ({diagram, index}) => {
                    const {default: mermaid} = await import('/mermaid/dist/mermaid.esm.min.mjs');
                    mermaid.initialize({startOnLoad: false, securityLevel: 'strict'});
                    await mermaid.parse(diagram);
                    const result = await mermaid.render('diagram' + index, diagram);
                    document.body.innerHTML = result.svg;
                    return result.svg;
                }""", {"diagram": diagram, "index": index})
                (args.output / f"diagram-{index}.svg").write_text(rendered)
                page.screenshot(path=str(args.output / f"diagram-{index}.png"), full_page=True)
                results.append({"diagram": index, "status": "PASS", "source_sha256": sha256(diagram.encode()).hexdigest(), "svg_sha256": sha256(rendered.encode()).hexdigest()})
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    report = {"mermaid": package["version"], "browser": args.browser, "diagrams": results}
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
