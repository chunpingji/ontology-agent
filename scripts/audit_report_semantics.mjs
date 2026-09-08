import ts from "../frontend/node_modules/typescript/lib/typescript.js";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const findings = [], files = [];
function scan(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const name = path.join(dir, entry.name);
    if (entry.isDirectory()) { scan(name); continue; }
    if (![".ts", ".tsx", ".js", ".jsx", ".mjs"].includes(path.extname(name))) continue;
    const text = fs.readFileSync(name, "utf8"), relative = path.relative(root, name);
    files.push({ path: relative, sha256: createHash("sha256").update(text).digest("hex") });
    const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);
    function visit(node) {
      let code;
      if (node.kind === ts.SyntaxKind.RegularExpressionLiteral) code = "REGEX_LITERAL";
      if (ts.isIdentifier(node) && ["RegExp", "eval", "Function"].includes(node.text)) code = "DYNAMIC_EXECUTION";
      if (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression) &&
          ["RegExp", "eval", "Function"].includes(node.argumentExpression.text)) code = "DYNAMIC_EXECUTION";
      if (code) findings.push({ path: relative, line: source.getLineAndCharacterOfPosition(node.pos).line + 1, code });
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
}
scan(path.join(root, "frontend/src"));
process.stdout.write(JSON.stringify({ findings, files }));
