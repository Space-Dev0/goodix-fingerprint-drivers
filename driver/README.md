# libfprint patch series

`goodix581a-sigfm.patch` is an eight-commit `git format-patch` series for the
GoodixTLS community tree. Its base is
`goodix-fp-linux-dev/libfprint` commit
`c343b69` (`chore: update email on license headers`).

Apply it from the root of a clean checkout at that base:

```sh
git am /path/to/goodix-fingerprint-drivers/driver/goodix581a-sigfm.patch
```

The series depends on that tree's existing GoodixTLS driver and is not a
standalone patch against current upstream libfprint. The upstream tree needs
the GoodixTLS foundation ported or accepted before this series can target it.

The standalone `driver/` sources mirror the driver and SIGFM sources carried
by the patch series. Update both when changing the implementation.
