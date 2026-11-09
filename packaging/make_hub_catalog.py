#!/usr/bin/env python3
"""Wrap the host's actual catalog using MCP Hub's reverse-websocket contract."""
import argparse
import json
import pathlib


def descriptor(tools):
    if not isinstance(tools, list) or not tools:
        raise ValueError('A nonempty host tool catalog is required')
    names = []
    for tool in tools:
        name = tool.get('name')
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('Invalid or duplicate tool name')
        names.append(name)
        if not isinstance(tool.get('input_schema'), dict):
            raise ValueError('Missing authoritative input schema')
        annotations = tool.get('annotations', {})
        if not all(isinstance(annotations.get(k), bool) for k in ('readOnlyHint', 'destructiveHint', 'openWorldHint')):
            raise ValueError('Missing MCP tool annotations')
    return dict(id='cantabile', namespace='cantabile', transport='reverse-websocket',
                product_id='flamoris.cantabile', connector_token_env='CANTABILE_CONNECTOR_TOKEN', tools=tools)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=pathlib.Path, required=True)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    content = descriptor(json.loads(args.tools.read_text()))
    with args.output.open('x') as output:
        json.dump(content, output, ensure_ascii=False, indent=2)
        output.write('\n')
