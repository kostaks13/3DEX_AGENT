"""
llama-cpp-python için GBNF grammar tanımı.
Modeli yalnızca geçerli JSON nesnesi üretmeye zorlar.
"""

from llama_cpp import LlamaGrammar

# JSON nesnesi üreten minimal GBNF grameri.
# Kök seviyede tek bir { ... } nesnesi zorunlu tutuluyor.
_JSON_GBNF = r"""
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws

object ::=
  "{" ws (
    string ":" ws value
    ("," ws string ":" ws value)*
  )? "}" ws

array  ::=
  "[" ws (
    value
    ("," ws value)*
  )? "]" ws

string ::=
  "\"" (
    [^\\"\x7F\x00-\x1F] |
    "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
  )* "\"" ws

number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? (("e" | "E") ("+" | "-")? [0-9]+)? ws

ws ::= ([ \t\n] ws)?
"""


def get_json_grammar() -> LlamaGrammar:
    return LlamaGrammar.from_string(_JSON_GBNF)
