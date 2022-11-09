// Board config for SparkFun MicroMod RP2040 + Ethernet Function Module

// Board and hardware specific configuration
#define MICROPY_HW_BOARD_NAME             "SparkFun MicroMod RP2040 + Ethernet Function Module"
// For 16MB Flash chip...
#define MICROPY_HW_FLASH_STORAGE_BYTES    (14336 * 1024)

// Enable networking.
#define MICROPY_PY_NETWORK                (1)

// Wiznet HW config.
#define MICROPY_HW_WIZNET_SPI_ID          (0)
#define MICROPY_HW_WIZNET_SPI_BAUDRATE    (20 * 1000 * 1000)
#define MICROPY_HW_WIZNET_SPI_SCK         (PICO_DEFAULT_SPI_SCK_PIN)      // SPI_SCK
#define MICROPY_HW_WIZNET_SPI_MOSI        (PICO_DEFAULT_SPI_TX_PIN)      // SPI_COPI
#define MICROPY_HW_WIZNET_SPI_MISO        (PICO_DEFAULT_SPI_RX_PIN)      // SPI_CIPO
#define MICROPY_HW_WIZNET_PIN_CS          (21)      // !CS0_PROCESSOR -> SPI_!CS -> G5
#define MICROPY_HW_WIZNET_PIN_RST         (13)      // ETH_!RST -> G2/PWM -> PWM0_PROCESSOR -> PWM0
// Connecting the INTN pin enables RECV interrupt handling of incoming data.
#define MICROPY_HW_WIZNET_PIN_INTN        (6)      // ETH_!INT -> G0_!INT -> D0_PROCESSOR -> D0

#define MICROPY_PY_UASYNCIO               (1)