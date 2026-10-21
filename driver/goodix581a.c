// Goodix 27c6:581a (Xiaomi GM168) driver for libfprint
// Reverse-engineered by Antigravity pair programming session

#include "drivers/goodixtls/goodix5xx.h"
#include "fp-device.h"
#include "fp-image-device.h"
#include "fp-image.h"
#include "fpi-assembling.h"
#include "fpi-context.h"
#include "fpi-image-device.h"
#include "fpi-image.h"
#include "fpi-ssm.h"
#include "glibconfig.h"
#include "gusb/gusb-device.h"
#include <stdio.h>
#include <stdlib.h>

#define FP_COMPONENT "goodixtls581a"

#include <glib.h>
#include <string.h>

#include "drivers_api.h"
#include "goodix.h"
#include "goodix_proto.h"
#include "goodix581a.h"

struct _FpiDeviceGoodixTls581a
{
  FpiDeviceGoodixTls5xx parent;

  GoodixTls5xxPix *calibration_img;
  FpiSsm *scan_ssm;
};

G_DECLARE_FINAL_TYPE (FpiDeviceGoodixTls581a, fpi_device_goodixtls581a, FPI,
                      DEVICE_GOODIXTLS581A, FpiDeviceGoodixTls5xx);

G_DEFINE_TYPE (FpiDeviceGoodixTls581a, fpi_device_goodixtls581a,
               FPI_TYPE_DEVICE_GOODIXTLS5XX);

enum activate_states {
  ACTIVATE_READ_AND_NOP,
  ACTIVATE_RESET,
  ACTIVATE_READ_REG,
  ACTIVATE_READ_OTP,
  ACTIVATE_CMD_D6,
  ACTIVATE_INIT_TLS,
  ACTIVATE_UPLOAD_MCU_CONFIG,
  ACTIVATE_CMD_C4_1,
  ACTIVATE_CMD_C4_2,
  ACTIVATE_CMD_D2,
  ACTIVATE_CMD_36,
  ACTIVATE_NUM_STATES,
};

enum scan_581a_states {
  SCAN_581A_CALIBRATE,
  SCAN_581A_ARM_TOUCH,
  SCAN_581A_CAPTURE,
  SCAN_581A_AWAIT_UP,
  SCAN_581A_NUM_STATES,
};

static void
on_tls_init_ready (FpDevice *dev, gpointer user_data, GError *error)
{
  FpiSsm *ssm = user_data;
  if (error)
    {
      fp_err ("581a TLS handshake failed: %s", error->message);
      fpi_ssm_mark_failed (ssm, error);
      return;
    }
  fp_dbg ("581a TLS Handshake successfully established!");
  fpi_ssm_next_state (ssm);
}

static void
activate_run_state (FpiSsm *ssm, FpDevice *dev)
{
  static const guint8 reset_payload[] = {0x05, 0x14};
  static const guint8 reg_payload[] = {0x00, 0x00, 0x00, 0x04, 0x00};
  static const guint8 otp_payload[] = {0x00, 0x00};
  static const guint8 d6_payload[] = {0x00, 0x00};
  static const guint8 c4_payload[] = {0x01, 0x00};
  static const guint8 d2_payload[] = {0x00, 0x00};

  switch (fpi_ssm_get_cur_state (ssm))
    {
    case ACTIVATE_READ_AND_NOP:
      goodix_start_read_loop (dev);
      goodix_send_nop (dev, goodixtls5xx_check_none, ssm);
      break;

    case ACTIVATE_RESET:
      goodix_send_protocol (dev, GOODIX_CMD_RESET, reset_payload, sizeof (reset_payload),
                            NULL, TRUE, 500, TRUE, goodixtls5xx_check_none_cmd, ssm);
      break;

    case ACTIVATE_READ_REG:
      goodix_send_protocol (dev, GOODIX_CMD_READ_SENSOR_REGISTER, reg_payload, sizeof (reg_payload),
                            NULL, TRUE, 500, TRUE, goodixtls5xx_check_none_cmd, ssm);
      break;

    case ACTIVATE_READ_OTP:
      goodix_send_protocol (dev, GOODIX_CMD_READ_OTP, otp_payload, sizeof (otp_payload),
                            NULL, TRUE, 500, TRUE, goodixtls5xx_check_none_cmd, ssm);
      break;

    case ACTIVATE_CMD_D6:
      goodix_send_protocol (dev, 0xd6, d6_payload, sizeof (d6_payload),
                            NULL, TRUE, 500, TRUE, goodixtls5xx_check_none_cmd, ssm);
      break;

    case ACTIVATE_INIT_TLS:
      goodix_tls_init (dev, on_tls_init_ready, ssm);
      break;

    case ACTIVATE_UPLOAD_MCU_CONFIG:
      goodix_send_upload_config_mcu (dev, (guint8 *) goodix_581a_config,
                                     sizeof (goodix_581a_config), NULL,
                                     goodixtls5xx_check_config_upload, ssm);
      break;

    case ACTIVATE_CMD_C4_1:
      goodix_send_protocol (dev, 0xc4, c4_payload, sizeof (c4_payload),
                            NULL, TRUE, 500, FALSE, goodixtls5xx_check_none_cmd, ssm);
      break;

    case ACTIVATE_CMD_C4_2:
      goodix_send_protocol (dev, 0xc4, c4_payload, sizeof (c4_payload),
                            NULL, TRUE, 500, FALSE, goodixtls5xx_check_none_cmd, ssm);
      break;

    case ACTIVATE_CMD_D2:
      goodix_send_protocol (dev, 0xd2, d2_payload, sizeof (d2_payload),
                            NULL, TRUE, 500, TRUE, goodixtls5xx_check_none_cmd, ssm);
      break;

    case ACTIVATE_CMD_36:
      goodix_send_protocol (dev, GOODIX_CMD_MCU_SWITCH_TO_FDT_MODE,
                            goodix_581a_payload_36, sizeof (goodix_581a_payload_36),
                            NULL, TRUE, 500, TRUE, goodixtls5xx_check_none_cmd, ssm);
      break;
    }
}

static void
activate_complete (FpiSsm *ssm, FpDevice *dev, GError *error)
{
  if (!error)
    {
      fp_dbg ("581a activation successfully completed!");
      fpi_image_device_activate_complete (FP_IMAGE_DEVICE (dev), NULL);
    }
  else
    {
      fp_err ("581a failed during activation: %s (code: %d)", error->message,
              error->code);
      fpi_image_device_activate_complete (FP_IMAGE_DEVICE (dev), error);
    }
}

static void
dev_activate (FpImageDevice *img_dev)
{
  FpDevice *dev = FP_DEVICE (img_dev);

  fpi_ssm_start (fpi_ssm_new (dev, activate_run_state, ACTIVATE_NUM_STATES),
                 activate_complete);
}

static void
on_581a_calib_read (FpDevice *dev, guint8 *data, guint16 length,
                    gpointer user_data, GError *error)
{
  FpiSsm *ssm = user_data;
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (dev);

  if (error)
    {
      fp_err ("581a: Failed to capture baseline image: %s", error->message);
      fpi_ssm_mark_failed (ssm, error);
      return;
    }

  self->calibration_img = g_malloc0 (GOODIX_581A_FRAME_SIZE * sizeof (GoodixTls5xxPix));
  goodixtls5xx_decode_frame (self->calibration_img, length, data, 0, 4);

  fp_dbg ("581a: Ambient baseline acquired successfully!");
  fpi_ssm_next_state (ssm);
}

static void
on_581a_touch_detected (FpDevice *dev, guint8 *data, guint16 length,
                        gpointer user_data, GError *error)
{
  FpiSsm *ssm = user_data;

  if (error)
    {
      fp_err ("581a: Touch interrupt error: %s", error->message);
      fpi_ssm_mark_failed (ssm, error);
      return;
    }

  fp_dbg ("581a: Hardware capacitive touch detected on sensor!");
  fpi_image_device_report_finger_status (FP_IMAGE_DEVICE (dev), TRUE);
  fpi_ssm_next_state (ssm);
}

static int
compare_ints (const void *a, const void *b)
{
  int arg1 = *(const int *) a;
  int arg2 = *(const int *) b;
  return (arg1 > arg2) - (arg1 < arg2);
}

static void
on_581a_mode5_read (FpDevice *dev, guint8 *data, guint16 length,
                    gpointer user_data, GError *error)
{
  FpiSsm *ssm = user_data;
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (dev);

  if (error)
    {
      fp_err ("581a: Failed to capture Mode 5 scan: %s", error->message);
      fpi_ssm_mark_failed (ssm, error);
      return;
    }

  GoodixTls5xxPix raw_frame[GOODIX_581A_FRAME_SIZE];
  goodixtls5xx_decode_frame (raw_frame, length, data, 0, 4);

  gint32 raw[GOODIX_581A_FRAME_SIZE];
  for (int i = 0; i < GOODIX_581A_FRAME_SIZE; i++)
    raw[i] = (gint32) raw_frame[i];

  /* 7-tap separable Gaussian/lowpass filter [1, 6, 15, 20, 15, 6, 1] / 64
   * directly on raw frame. Removes the slow capacitive pad curvature
   * while preserving 100% of the true ridge/valley sinusoidal frequency.
   */
  gint32 tmp[GOODIX_581A_FRAME_SIZE];
  for (int y = 0; y < GOODIX_581A_HEIGHT; y++)
    {
      for (int x = 0; x < GOODIX_581A_WIDTH; x++)
        {
          int x0 = CLAMP (x - 3, 0, GOODIX_581A_WIDTH - 1);
          int x1 = CLAMP (x - 2, 0, GOODIX_581A_WIDTH - 1);
          int x2 = CLAMP (x - 1, 0, GOODIX_581A_WIDTH - 1);
          int x3 = x;
          int x4 = CLAMP (x + 1, 0, GOODIX_581A_WIDTH - 1);
          int x5 = CLAMP (x + 2, 0, GOODIX_581A_WIDTH - 1);
          int x6 = CLAMP (x + 3, 0, GOODIX_581A_WIDTH - 1);

          tmp[y * GOODIX_581A_WIDTH + x] = (raw[y * GOODIX_581A_WIDTH + x0] * 1 +
                                            raw[y * GOODIX_581A_WIDTH + x1] * 6 +
                                            raw[y * GOODIX_581A_WIDTH + x2] * 15 +
                                            raw[y * GOODIX_581A_WIDTH + x3] * 20 +
                                            raw[y * GOODIX_581A_WIDTH + x4] * 15 +
                                            raw[y * GOODIX_581A_WIDTH + x5] * 6 +
                                            raw[y * GOODIX_581A_WIDTH + x6] * 1) >> 6;
        }
    }

  gint32 bg[GOODIX_581A_FRAME_SIZE];
  for (int x = 0; x < GOODIX_581A_WIDTH; x++)
    {
      for (int y = 0; y < GOODIX_581A_HEIGHT; y++)
        {
          int y0 = CLAMP (y - 3, 0, GOODIX_581A_HEIGHT - 1);
          int y1 = CLAMP (y - 2, 0, GOODIX_581A_HEIGHT - 1);
          int y2 = CLAMP (y - 1, 0, GOODIX_581A_HEIGHT - 1);
          int y3 = y;
          int y4 = CLAMP (y + 1, 0, GOODIX_581A_HEIGHT - 1);
          int y5 = CLAMP (y + 2, 0, GOODIX_581A_HEIGHT - 1);
          int y6 = CLAMP (y + 3, 0, GOODIX_581A_HEIGHT - 1);

          bg[y * GOODIX_581A_WIDTH + x] = (tmp[y0 * GOODIX_581A_WIDTH + x] * 1 +
                                           tmp[y1 * GOODIX_581A_WIDTH + x] * 6 +
                                           tmp[y2 * GOODIX_581A_WIDTH + x] * 15 +
                                           tmp[y3 * GOODIX_581A_WIDTH + x] * 20 +
                                           tmp[y4 * GOODIX_581A_WIDTH + x] * 15 +
                                           tmp[y5 * GOODIX_581A_WIDTH + x] * 6 +
                                           tmp[y6 * GOODIX_581A_WIDTH + x] * 1) >> 6;
        }
    }

  gint32 hp[GOODIX_581A_FRAME_SIZE];
  gint32 active_vals[GOODIX_581A_FRAME_SIZE];
  int n_active = 0;

  for (int y = 0; y < GOODIX_581A_HEIGHT; y++)
    {
      for (int x = 0; x < GOODIX_581A_WIDTH; x++)
        {
          int idx = y * GOODIX_581A_WIDTH + x;
          hp[idx] = raw[idx] - bg[idx];

          if (x >= 2 && x < GOODIX_581A_WIDTH - 2 &&
              y >= 2 && y < GOODIX_581A_HEIGHT - 2)
            {
              active_vals[n_active++] = hp[idx];
            }
        }
    }

  gint32 p5 = -20, p95 = 20;
  if (n_active > 50)
    {
      qsort (active_vals, n_active, sizeof (gint32), compare_ints);
      p5 = active_vals[n_active * 5 / 100];
      p95 = active_vals[n_active * 95 / 100];
      if (p95 - p5 < 40)
        {
          p5 = -20;
          p95 = 20;
        }
    }

  fp_dbg ("581a: Ridge contrast range: p5=%d, p95=%d, active=%d pixels", p5, p95, n_active);

  FpImage *img = fp_image_new (GOODIX_581A_WIDTH, GOODIX_581A_HEIGHT);
  img->flags |= FPI_IMAGE_PARTIAL;
  img->ppmm = 500.0 / 25.4;

  for (int y = 0; y < GOODIX_581A_HEIGHT; y++)
    {
      for (int x = 0; x < GOODIX_581A_WIDTH; x++)
        {
          int idx = y * GOODIX_581A_WIDTH + x;
          if (x < 2 || x >= GOODIX_581A_WIDTH - 2 ||
              y < 2 || y >= GOODIX_581A_HEIGHT - 2)
            {
              img->data[idx] = 255;
            }
          else
            {
              gint32 val = (hp[idx] - p5) * 255 / (p95 - p5);
              if (val < 0) val = 0;
              if (val > 255) val = 255;
              img->data[idx] = (guint8) (255 - val);
            }
        }
    }

  fp_dbg ("581a: Frame processed, reporting native 64x80 image captured for SIGFM!");
  img->ppmm = 500.0 / 25.4;
  fpi_image_device_image_captured (FP_IMAGE_DEVICE (dev), img);

  if (!self->scan_ssm)
    return;

  /* Advance to AWAIT_UP to wait for user to physically remove their finger */
  fpi_ssm_next_state (ssm);
}

static void
on_581a_touch_up (FpDevice *dev, guint8 *data, guint16 length,
                  gpointer user_data, GError *error)
{
  FpiSsm *ssm = user_data;
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (dev);

  if (error)
    {
      fp_err ("581a: Touch up error: %s", error->message);
      fpi_ssm_mark_failed (ssm, error);
      return;
    }

  fp_dbg ("581a: Finger physically lifted from sensor!");
  /* Keep calibration_img intact across touches to maintain stable ambient air baseline */
  self->scan_ssm = NULL;
  fpi_ssm_mark_completed (ssm);

  fpi_image_device_report_finger_status (FP_IMAGE_DEVICE (dev), FALSE);
}

static void
scan_581a_run_state (FpiSsm *ssm, FpDevice *dev)
{
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (dev);

  switch (fpi_ssm_get_cur_state (ssm))
    {
    case SCAN_581A_CALIBRATE:
      if (self->calibration_img)
        {
          fpi_ssm_next_state (ssm);
          return;
        }
      fp_dbg ("581a: Reading ambient baseline...");
      goodix_tls_read_image (dev, goodix_581a_get_image_mode1,
                             sizeof (goodix_581a_get_image_mode1),
                             on_581a_calib_read, ssm);
      break;

    case SCAN_581A_ARM_TOUCH:
      fp_dbg ("581a: Arming FDT touch detection...");
      goodix_send_protocol (dev, GOODIX_CMD_MCU_SWITCH_TO_FDT_DOWN,
                            goodix_581a_fdt_down, sizeof (goodix_581a_fdt_down),
                            NULL, TRUE, 0, TRUE, on_581a_touch_detected, ssm);
      break;

    case SCAN_581A_CAPTURE:
      fp_dbg ("581a: Capturing Mode 5 image...");
      goodix_tls_read_image (dev, goodix_581a_get_image_mode5,
                             sizeof (goodix_581a_get_image_mode5),
                             on_581a_mode5_read, ssm);
      break;

    case SCAN_581A_AWAIT_UP:
      fp_dbg ("581a: Arming FDT finger release detection...");
      goodix_send_protocol (dev, GOODIX_CMD_MCU_SWITCH_TO_FDT_UP,
                            goodix_581a_fdt_up, sizeof (goodix_581a_fdt_up),
                            NULL, TRUE, 0, TRUE, on_581a_touch_up, ssm);
      break;
    }
}

static void
scan_581a_complete (FpiSsm *ssm, FpDevice *dev, GError *error)
{
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (dev);
  self->scan_ssm = NULL;

  if (error)
    {
      if (!g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
        {
          fp_err ("581a scan failed: %s (code: %d)", error->message, error->code);
          fpi_image_device_session_error (FP_IMAGE_DEVICE (dev), error);
        }
      return;
    }

  fp_dbg ("581a scan cycle successfully finished");
}

static void
dev_581a_change_state (FpImageDevice *img_dev, FpiImageDeviceState state)
{
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (img_dev);
  FpDevice *dev = FP_DEVICE (img_dev);

  if (state == FPI_IMAGE_DEVICE_STATE_AWAIT_FINGER_ON)
    {
      fp_dbg ("581a entering AWAIT_FINGER_ON state");
      self->scan_ssm = fpi_ssm_new (dev, scan_581a_run_state, SCAN_581A_NUM_STATES);
      fpi_ssm_start (self->scan_ssm, scan_581a_complete);
    }
}

static void
dev_581a_deactivate (FpImageDevice *img_dev)
{
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (img_dev);
  FpDevice *dev = FP_DEVICE (img_dev);
  GError *error = NULL;

  if (self->scan_ssm)
    {
      FpiSsm *ssm = self->scan_ssm;
      self->scan_ssm = NULL;
      fpi_ssm_mark_failed (ssm, g_error_new (G_IO_ERROR, G_IO_ERROR_CANCELLED, "Device deactivated"));
    }

  goodix_reset_state (dev);
  goodix_shutdown_tls (dev, &error);

  fpi_image_device_deactivate_complete (img_dev, error);
}

static void
fpi_device_goodixtls581a_init (FpiDeviceGoodixTls581a *self)
{
  self->calibration_img = NULL;
  self->scan_ssm = NULL;
}

static void
fpi_device_goodixtls581a_finalize (GObject *object)
{
  FpiDeviceGoodixTls581a *self = FPI_DEVICE_GOODIXTLS581A (object);

  g_clear_pointer (&self->calibration_img, g_free);

  G_OBJECT_CLASS (fpi_device_goodixtls581a_parent_class)->finalize (object);
}

static GoodixTls5xxMcuConfig
get_mcu_config (void)
{
  GoodixTls5xxMcuConfig cfg;

  cfg.free_fn = NULL;
  cfg.data = (guint8 *) goodix_581a_fdt_down;
  cfg.data_len = sizeof (goodix_581a_fdt_down);
  return cfg;
}

static FpImage *
process_frame (guint8 * frame)
{
  FpImage * img = fp_image_new (GOODIX_581A_WIDTH, GOODIX_581A_HEIGHT);

  img->flags |= FPI_IMAGE_PARTIAL;
  for (int y = 0; y != GOODIX_581A_HEIGHT; ++y)
    {
      for (int x = 0; x != GOODIX_581A_WIDTH; ++x)
        {
          const int idx = x + y * GOODIX_581A_WIDTH;
          img->data[idx] = 255 - frame[idx];
        }
    }
  return img;
}

static void
fpi_device_goodixtls581a_class_init (FpiDeviceGoodixTls581aClass * class)
{
  GObjectClass * gobject_class = G_OBJECT_CLASS (class);
  FpiDeviceGoodixTlsClass * gx_class = FPI_DEVICE_GOODIXTLS_CLASS (class);
  FpDeviceClass * dev_class = FP_DEVICE_CLASS (class);
  FpImageDeviceClass * img_dev_class = FP_IMAGE_DEVICE_CLASS (class);
  FpiDeviceGoodixTls5xxClass * xx_cls = FPI_DEVICE_GOODIXTLS5XX_CLASS (class);

  gobject_class->finalize = fpi_device_goodixtls581a_finalize;

  xx_cls->get_mcu_cfg = get_mcu_config;
  xx_cls->process_frame = process_frame;
  xx_cls->scan_height = GOODIX_581A_HEIGHT;
  xx_cls->scan_width = GOODIX_581A_WIDTH;
  xx_cls->raw_header_len = 0;
  xx_cls->raw_footer_len = 4;
  xx_cls->calib_img_payload = goodix_581a_get_image_mode1;
  xx_cls->calib_img_payload_len = sizeof (goodix_581a_get_image_mode1);
  xx_cls->get_img_payload = goodix_581a_get_image_mode5;
  xx_cls->get_img_payload_len = sizeof (goodix_581a_get_image_mode5);

  gx_class->interface = GOODIX_581A_INTERFACE;
  gx_class->ep_in = GOODIX_581A_EP_IN;
  gx_class->ep_out = GOODIX_581A_EP_OUT;

  dev_class->id = "goodixtls581a";
  dev_class->full_name = "Goodix TLS Fingerprint Sensor 581a (Xiaomi GM168)";
  dev_class->type = FP_DEVICE_TYPE_USB;
  dev_class->id_table = id_table;
  dev_class->nr_enroll_stages = 12;
  dev_class->scan_type = FP_SCAN_TYPE_PRESS;

  img_dev_class->bz3_threshold = 24;
  img_dev_class->img_width = GOODIX_581A_WIDTH;
  img_dev_class->img_height = GOODIX_581A_HEIGHT;
  img_dev_class->use_sigfm = TRUE;
  img_dev_class->activate = dev_activate;
  img_dev_class->deactivate = dev_581a_deactivate;
  img_dev_class->change_state = dev_581a_change_state;

  fpi_device_class_auto_initialize_features (dev_class);
}
