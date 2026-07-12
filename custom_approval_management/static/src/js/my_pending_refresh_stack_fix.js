/** @odoo-module **/

import { registry } from "@web/core/registry";

const services = registry.category("services");
const actionService = services.get("action");

if (actionService && !actionService.__customApprovalRefreshPatched) {
    const originalStart = actionService.start;

    actionService.start = function (env, ...args) {
        const service = originalStart.call(this, env, ...args);
        const originalDoActionButton = service.doActionButton.bind(service);

        service.doActionButton = (params, options = {}) => {
            if (
                params?.type === "object" &&
                params?.name === "action_refresh_my_pending_approvals" &&
                !params?.stackPosition
            ) {
                params = {
                    ...params,
                    stackPosition: "replaceCurrentAction",
                };
            }
            return originalDoActionButton(params, options);
        };

        return service;
    };

    actionService.__customApprovalRefreshPatched = true;
}