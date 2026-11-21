import os

from openminion.tools import Plugin, ToolDecl, ToolFamilySpec, build_registrar
from pydantic import BaseModel

from order_care.app import OrderStore


class ReadOrderArgs(BaseModel):
    order_id: str


class ApplyStateArgs(ReadOrderArgs):
    state: str


def read_state(args, context):
    del context
    state = OrderStore(os.environ["ORDER_CARE_DB"]).order_state(args["order_id"])
    return {"order_id": args["order_id"], "state": state}


def apply_state(args, context):
    del context
    store = OrderStore(os.environ["ORDER_CARE_DB"])
    store.put_order(args["order_id"], args["state"])
    return {"order_id": args["order_id"], "state": args["state"]}


REGISTRAR = build_registrar(
    ToolFamilySpec(
        module_id="example.order_care",
        tools=(
            ToolDecl(
                name="plugin.example.order_care.read_state",
                args_model=ReadOrderArgs,
                handler=read_state,
                description="Read one order state from the example application.",
                min_scope="READ_ONLY",
                idempotent=True,
            ),
            ToolDecl(
                name="plugin.example.order_care.apply_state",
                args_model=ApplyStateArgs,
                handler=apply_state,
                description="Apply one approved order-state change.",
                dangerous=True,
                idempotent=True,
            ),
        ),
    )
)


class OrderCarePlugin(Plugin):
    name = "order-care"
