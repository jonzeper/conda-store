import pydantic
from typing import List, Dict, Optional

import yaml
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from conda_store_server import api, orm, schema, utils
from conda_store_server.server import dependencies
from conda_store_server.server.auth import Permissions


router_api = APIRouter(
    tags=['api'],
    prefix='/api/v1',
)


def get_paginated_args(
        page: int = 1,
        order: Optional[str] = None,
        size: Optional[int] = None,
        distinct_on: List[str] = Query([]),
        sort_by: List[str] = Query([]),
        server = Depends(dependencies.get_server),
):
    if size is None:
        size = server.max_page_size
    size = min(size, server.max_page_size)
    offset = (page - 1) * size
    return {
        'limit': size,
        'offset': offset,
        'distinct_on': distinct_on,
        'sort_by': sort_by,
        'order': order,
    }



def filter_distinct_on(
        query,
        distinct_on: List[str] = [],
        allowed_distinct_ons: Dict = {},
        default_distinct_on: List[str] = []
):
    distinct_on = distinct_on or default_distinct_on
    distinct_on = [
        allowed_distinct_ons[d] for d in distinct_on if d in allowed_distinct_ons
    ]

    if distinct_on:
        return distinct_on, query.distinct(*distinct_on)
    return distinct_on, query


def get_sorts(
    order: str,
    sort_by: List[str] = [],
    allowed_sort_bys: Dict = {},
    required_sort_bys: List = [],
    default_sort_by: List = [],
    default_order: str = "asc",
):
    sort_by = sort_by or default_sort_by
    sort_by = [allowed_sort_bys[s] for s in sort_by if s in allowed_sort_bys]

    # required_sort_bys is needed when sorting is used with distinct
    # query see "SELECT DISTINCT ON expressions must match initial
    # ORDER BY expressions"
    if required_sort_bys != sort_by[: len(required_sort_bys)]:
        sort_by = required_sort_bys + sort_by

    if order not in {"asc", "desc"}:
        order = default_order

    order_mapping = {"asc": lambda c: c.asc(), "desc": lambda c: c.desc()}
    return [order_mapping[order](k) for k in sort_by]


def paginated_api_response(
    query,
    paginated_args,
    object_schema,
    sorts: List = [],
    exclude=None,
    allowed_sort_bys: Dict = {},
    required_sort_bys: List = [],
    default_sort_by: List = [],
    default_order: str = "asc",
):

    sorts = get_sorts(
        order=paginated_args['order'],
        sort_by=paginated_args['sort_by'],
        allowed_sort_bys=allowed_sort_bys,
        required_sort_bys=required_sort_bys,
        default_sort_by=default_sort_by,
        default_order=default_order,
    )

    count = query.count()
    query = query.order_by(*sorts).limit(paginated_args['limit']).offset(paginated_args['offset'])

    return {
            "status": "ok",
        "data": [
            object_schema.from_orm(_).dict(exclude=exclude) for _ in query.all()
        ],
        "page": (paginated_args['offset'] // paginated_args['limit']) + 1,
        "size": paginated_args['limit'],
        "count": count,
    }


@router_api.get("/")
def api_status():
    return {"status": "ok"}


@router_api.get("/permission/")
def api_get_permissions(
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
        entity = Depends(dependencies.get_entity),
):
    authenticated = entity is not None
    entity_binding_permissions = auth.authorization.get_entity_binding_permissions(
        entity.role_bindings if authenticated else {}, authenticated=authenticated
    )

    # convert Dict[str, set[enum]] -> Dict[str, List[str]]
    # to be json serializable
    entity_binding_permissions = {
        entity_arn: [_.value for _ in entity_permissions]
        for entity_arn, entity_permissions in entity_binding_permissions.items()
    }

    return {
        "status": "ok",
        "data": {
            "authenticated": authenticated,
            "entity_permissions": entity_binding_permissions,
            "primary_namespace": entity.primary_namespace
                if authenticated
                else conda_store.default_namespace,
        },
    }


@router_api.get("/namespace/")
def api_list_namespaces(
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
        entity = Depends(dependencies.get_entity),
        paginated_args: Dict = Depends(get_paginated_args),
):
    orm_namespaces = auth.filter_namespaces(
        entity,
        api.list_namespaces(conda_store.db, show_soft_deleted=False)
    )
    return paginated_api_response(
        orm_namespaces,
        paginated_args,
        schema.Namespace,
        allowed_sort_bys={
            "name": orm.Namespace.name,
        },
        default_sort_by=["name"],
    )


@router_api.get("/namespace/{namespace}/")
def api_get_namespace(
        namespace: str,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    auth.authorize_request(request, namespace, {Permissions.NAMESPACE_READ}, require=True)

    namespace = api.get_namespace(conda_store.db, namespace)
    if namespace is None:
        return HTTPException(status_code=404, details="namespace does not exist")

    return {
        "status": "ok",
        "data": schema.Namespace.from_orm(namespace).dict(),
    }


@router_api.post("/namespace/{namespace}/")
def api_create_namespace(
        namespace: str,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    auth.authorize_request(request, namespace, {Permissions.NAMESPACE_CREATE}, require=True)

    namespace_orm = api.get_namespace(conda_store.db, namespace)
    if namespace_orm:
        return HTTPException(status_code=409, details="namespace already exists")

    try:
        api.create_namespace(conda_store.db, namespace)
    except ValueError as e:
        return HTTPException(status_code=400, detail=str(e.args[0]))
    conda_store.db.commit()
    return {"status": "ok"}


@router_api.delete("/namespace/{namespace}/")
def api_delete_namespace(
        namespace: str,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    auth.authorize_request(namespace, {Permissions.NAMESPACE_DELETE}, require=True)

    namespace_orm = api.get_namespace(conda_store.db, namespace)
    if namespace_orm is None:
        return HTTPException(status_code=404, details="namespace does not exist")

    conda_store.delete_namespace(namespace)
    return {"status": "ok"}


@router_api.get("/environment/")
def api_list_environments(
        search: Optional[str] = None,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
        entity = Depends(dependencies.get_entity),
        paginated_args = Depends(get_paginated_args)
):
    orm_environments = auth.filter_environments(
        entity,
        api.list_environments(conda_store.db, search=search, show_soft_deleted=False)
    )
    return paginated_api_response(
        orm_environments,
        paginated_args,
        schema.Environment,
        exclude={"current_build"},
        allowed_sort_bys={
            "namespace": orm.Namespace.name,
            "name": orm.Environment.name,
        },
        default_sort_by=["namespace", "name"],
    )


@router_api.get("/environment/{namespace}/{name}/")
def api_get_environment(
        namespace: str,
        name: str,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
        entity = Depends(dependencies.get_entity),
):
    auth.authorize_request(
        entity,
        f"{namespace}/{name}", {Permissions.ENVIRONMENT_READ}, require=True
    )

    environment = api.get_environment(conda_store.db, namespace=namespace, name=name)
    if environment is None:
        return HTTPException(
            status_code=404,
            details="environment does not exist"
        )

    return {
        "status": "ok",
        "data": schema.Environment.from_orm(environment).dict(
            exclude={"current_build"}
        ),
    }


@router_api.put("/environment/{namespace}/{name}/")
def api_update_environment_build(
        namespace: str,
        name: str,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
        entity = Depends(dependencies.get_entity),
):
    auth.authorize_request(
        entity,
        f"{namespace}/{name}", {Permissions.ENVIRONMENT_UPDATE}, require=True
    )

    # replace with https://fastapi.tiangolo.com/tutorial/body/
    data = request.json
    if "build_id" not in data:
        return jsonify({"status": "error", "message": "build id not specificated"}), 400

    try:
        build_id = data["build_id"]
        conda_store.update_environment_build(namespace, name, build_id)
    except utils.CondaStoreError as e:
        return e.response

    return {"status": "ok"}


@router_api.delete("/environment/{namespace}/{name}/")
def api_delete_environment(
        namespace: str,
        name: str,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
        entity = Depends(dependencies.get_entity),
):
    auth.authorize_request(
        entity,
        f"{namespace}/{name}", {Permissions.ENVIRONMENT_DELETE}, require=True
    )

    conda_store.delete_environment(namespace, name)
    return {"status": "ok"}


# @app_api.route("/api/v1/specification/", methods=["POST"])
# def api_post_specification():
#     conda_store = get_conda_store()
#     auth = get_auth()

#     permissions = {Permissions.ENVIRONMENT_CREATE}

#     entity = auth.authenticate_request()
#     default_namespace = (
#         entity.primary_namespace if entity else conda_store.default_namespace
#     )

#     namespace_name = request.json.get("namespace") or default_namespace
#     namespace = api.get_namespace(conda_store.db, namespace_name)
#     if namespace is None:
#         permissions.add(Permissions.NAMESPACE_CREATE)

#     try:
#         specification = request.json.get("specification")
#         specification = yaml.safe_load(specification)
#         specification = schema.CondaSpecification.parse_obj(specification)
#     except yaml.error.YAMLError:
#         return (
#             jsonify({"status": "error", "message": "Unable to parse. Invalid YAML"}),
#             400,
#         )
#     except pydantic.ValidationError as e:
#         return jsonify({"status": "error", "message": str(e)}), 400

#     auth.authorize_request(
#         f"{namespace_name}/{specification.name}",
#         permissions,
#         require=True,
#     )

#     try:
#         build_id = api.post_specification(conda_store, specification, namespace_name)
#     except ValueError as e:
#         return jsonify({"status": "error", "message": str(e.args[0])}), 400

#     return jsonify({"status": "ok", "data": {"build_id": build_id}})


@router_api.get("/build/")
def api_list_builds(
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
        entity = Depends(dependencies.get_entity),
):
    orm_builds = auth.filter_builds(
        entity,
        api.list_builds(conda_store.db, show_soft_deleted=True)
    )
    return paginated_api_response(
        orm_builds,
        schema.Build,
        exclude={"specification", "packages"},
        allowed_sort_bys={
            "id": orm.Build.id,
        },
        default_sort_by=["id"],
    )


@router_api.get("/build/{build_id}/")
def api_get_build(
        build_id: int,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    build = api.get_build(conda_store.db, build_id)
    if build is None:
        return HTTPException(status_code=404, details="build id does not exist")

    auth.authorize_request(
        request,
        f"{build.environment.namespace.name}/{build.environment.name}",
        {Permissions.ENVIRONMENT_READ},
        require=True,
    )

    return {
        "status": "ok",
        "data": schema.Build.from_orm(build).dict(exclude={"packages"}),
    }


@router_api.put("/build/<build_id>/")
def api_put_build(
        build_id: int,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    build = api.get_build(conda_store.db, build_id)
    if build is None:
        return HTTPException(status_code=404, detail="build id does not exist")

    auth.authorize_request(
        request,
        f"{build.environment.namespace.name}/{build.environment.name}",
        {Permissions.ENVIRONMENT_UPDATE},
        require=True,
    )

    conda_store.create_build(build.environment_id, build.specification.sha256)
    return {"status": "ok", "message": "rebuild triggered"}


@router_api.delete("/build/{build_id}/")
def api_delete_build(
        build_id: int,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    build = api.get_build(conda_store.db, build_id)
    if build is None:
        return HTTPException(status_code=404, detail="build id does not exist")

    auth.authorize_request(
        request,
        f"{build.environment.namespace.name}/{build.environment.name}",
        {Permissions.BUILD_DELETE},
        require=True,
    )

    conda_store.delete_build(build_id)
    return {"status": "ok"}


@router_api.get("/build/{build_id}/packages/")
def api_get_build_packages(
        build_id: int,
        search: str,
        exact: str,
        build: str,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    build_orm = api.get_build(conda_store.db, build_id)
    if build_orm is None:
        return HTTPException(status_code=404, detail="build id does not exist")

    auth.authorize_request(
        request,
        f"{build_orm.environment.namespace.name}/{build_orm.environment.name}",
        {Permissions.ENVIRONMENT_READ},
        require=True,
    )
    orm_packages = api.get_build_packages(
        conda_store.db, build_orm.id, search=search, exact=exact, build=build
    )
    return paginated_api_response(
        orm_packages,
        schema.CondaPackage,
        allowed_sort_bys={
            "channel": orm.CondaChannel.name,
            "name": orm.CondaPackage.name,
        },
        default_sort_by=["channel", "name"],
        exclude={"channel": {"last_update"}},
    )


@router_api.get("/build/{build_id}/logs/")
def api_get_build_logs(
        build_id: int,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    build = api.get_build(conda_store.db, build_id)
    if build is None:
        return HTTPException(status_code=404, detail="build id does not exist")

    auth.authorize_request(
        request,
        f"{build.environment.namespace.name}/{build.environment.name}",
        {Permissions.ENVIRONMENT_READ},
        require=True,
    )

    return RedirectResponse(conda_store.storage.get_url(build.log_key))


@router_api.get("/channel/")
def api_list_channels(
        conda_store = Depends(dependencies.get_conda_store),
):
    orm_channels = api.list_conda_channels(conda_store.db)
    return paginated_api_response(
        orm_channels,
        schema.CondaChannel,
        allowed_sort_bys={"name": orm.CondaChannel.name},
        default_sort_by=["name"],
    )


@router_api.get("/package/")
def api_list_packages(
        search: Optional[str],
        exact: Optional[str],
        build: Optional[str],
        conda_store = Depends(dependencies.get_conda_store),
):
    orm_packages = api.list_conda_packages(
        conda_store.db, search=search, exact=exact, build=build
    )
    required_sort_bys, distinct_orm_packages = filter_distinct_on(
        orm_packages,
        allowed_distinct_ons={
            "channel": orm.CondaChannel.name,
            "name": orm.CondaPackage.name,
            "version": orm.CondaPackage.version,
        },
    )
    return paginated_api_response(
        distinct_orm_packages,
        schema.CondaPackage,
        allowed_sort_bys={
            "channel": orm.CondaChannel.name,
            "name": orm.CondaPackage.name,
            "version": orm.CondaPackage.version,
            "build": orm.CondaPackage.build,
        },
        default_sort_by=["channel", "name", "version", "build"],
        required_sort_bys=required_sort_bys,
        exclude={"channel": {"last_update"}},
    )


@router_api.get("/build/{build_id}/yaml/")
def api_get_build_yaml(
        build_id: int,
        request: Request,
        conda_store = Depends(dependencies.get_conda_store),
        auth = Depends(dependencies.get_auth),
):
    build = api.get_build(conda_store.db, build_id)
    if build is None:
        return HTTPException(status_code=404, detail="build id does not exist")

    auth.authorize_request(
        request,
        f"{build.environment.namespace.name}/{build.environment.name}",
        {Permissions.ENVIRONMENT_READ},
        require=True,
    )
    return RedirectResponse(conda_store.storage.get_url(build.conda_env_export_key))
