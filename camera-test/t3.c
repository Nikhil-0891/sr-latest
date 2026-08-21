#include <stdio.h>
#include "pv_rhino.h"

int main() {

//    const char *access_key = "DrXmrvu3jdmujWvWdz/9d0T3sZ4fP26i+8DS6vc5BUCp8nd9zHM3EQ==";
const char *access_key = "test";
    const char *model_path =
    "rhino/lib/common/rhino_params.pv";

    const char *context_path =
    "rhino/resources/Camera_en_mac_v4_0_0.rhn";

    pv_rhino_t *rhino = NULL;

    printf("Initializing Rhino...\n");

    pv_status_t status = pv_rhino_init(
        access_key,
        model_path,
        NULL,
        context_path,
        0.5f,
        1.0f,
        false,
        &rhino
    );

    printf("Status = %d\n", status);

    if (status != PV_STATUS_SUCCESS) {
        printf("Rhino init failed\n");
        return 1;
    }

    printf("Rhino initialized successfully\n");

    pv_rhino_delete(rhino);

    return 0;
}
